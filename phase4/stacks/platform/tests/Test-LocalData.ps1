[CmdletBinding()]
param(
    [string]$Docker = 'docker',
    [string]$PostgresImage = 'postgres:17-alpine',
    [string]$MongoImage = 'mongo:8.0',
    [string]$RabbitImage = 'rabbitmq:4-management-alpine',
    [string]$AuthImage = 'oficina-auth-internal:9.2.3.1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$testOwner = 'oficina-9232-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$containers = @()
$volumes = @()
$network = $null
$scripts = (Resolve-Path (Join-Path $PSScriptRoot '../scripts')).Path
$testScripts = (Resolve-Path $PSScriptRoot).Path
$checks = 0

function Invoke-Docker {
    param([string[]]$Arguments, [switch]$ExpectFailure)
    $ErrorActionPreference = 'Continue'
    $result = & $Docker @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if (($ExpectFailure -and $exitCode -eq 0) -or (!$ExpectFailure -and $exitCode -ne 0)) {
        throw "Unexpected Docker exit code $exitCode for $($Arguments[0]); local fixture $testOwner; check $script:stage"
    }
    return ($result -join "`n")
}

function Wait-Container {
    param([string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        $null = & $Docker @Arguments 2>&1
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    throw "Local dependency did not become ready: $testOwner"
}

function Assert-Value {
    param([string]$Actual, [string]$Expected)
    if ($Actual.Trim() -cne $Expected) { throw "Local assertion failed: expected $Expected, received $Actual" }
    $script:checks++
}

try {
    $script:stage = 'postgres startup'
    $tlsVolume = "$testOwner-pg-tls"
    $caVolume = "$testOwner-pg-ca"
    foreach ($volume in @($tlsVolume, $caVolume)) {
        $null = Invoke-Docker @('volume', 'create', '--label', "oficina.test-owner=$testOwner", $volume)
        $volumes += $volume
    }
    $null = Invoke-Docker @('run', '--rm', '--user', '0:0', '--label', "oficina.test-owner=$testOwner",
        '-v', "${tlsVolume}:/fixture", '-v', "${caVolume}:/public", '--entrypoint', 'sh', $AuthImage, '-ec',
        'openssl req -x509 -newkey rsa:2048 -nodes -keyout /fixture/server.key -out /fixture/server.crt -days 1 -subj /CN=localhost -addext subjectAltName=DNS:localhost,IP:127.0.0.1 >/dev/null 2>&1; cp /fixture/server.crt /public/root.crt; chmod 644 /public/root.crt /fixture/server.crt; chmod 600 /fixture/server.key; chown 70:70 /fixture/server.key')
    $postgres = "$testOwner-pg"
    $containers += $postgres
    $null = Invoke-Docker @('run', '-d', '--name', $postgres, '--label', "oficina.test-owner=$testOwner",
        '-v', "${tlsVolume}:/tls:ro", '-e', 'POSTGRES_PASSWORD=local-admin-only', '-e', 'POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256',
        $PostgresImage, '-c', 'ssl=on', '-c', 'ssl_cert_file=/tls/server.crt', '-c', 'ssl_key_file=/tls/server.key')
    Wait-Container @('exec', $postgres, 'pg_isready', '-U', 'postgres')
    $null = Invoke-Docker @('cp', (Join-Path $scripts 'bootstrap.sql'), "${postgres}:/tmp/bootstrap.sql")
    $bootstrap = @('exec', '-e', 'AUTH_DB_NAME=local_auth', '-e', 'AUTH_DB_USER=local_auth',
        '-e', "AUTH_DB_PASSWORD=local-auth-'quoted", '-e', 'AUTH_MIGRATION_DB_USER=local_auth_migrator',
        '-e', 'AUTH_MIGRATION_DB_PASSWORD=local-migration-only', '-e', 'OS_DB_NAME=local_os', '-e', 'OS_DB_USER=local_os',
        '-e', 'OS_DB_PASSWORD=local-os-only', '-e', 'BILLING_DB_NAME=local_billing', '-e', 'BILLING_DB_USER=local_billing',
        '-e', 'BILLING_DB_PASSWORD=local-billing-only', $postgres, 'psql', '-Xq', '-U', 'postgres', '-f', '/tmp/bootstrap.sql')
    $null = Invoke-Docker @('exec', $postgres, 'psql', '-U', 'postgres', '-c', 'CREATE DATABASE local_billing OWNER postgres')
    $script:stage = 'reject unexpected database owner'
    $failureOutput = Invoke-Docker -Arguments $bootstrap -ExpectFailure
    $null = Invoke-Docker @('exec', $postgres, 'psql', '-U', 'postgres', '-c', 'DROP DATABASE local_billing')
    $firstOutput = Invoke-Docker $bootstrap
    $secondOutput = Invoke-Docker $bootstrap
    $script:stage = 'Auth MigrateIdentity verify-full with default JDBC CA location'
    $migration = @('run', '--rm', '--label', "oficina.test-owner=$testOwner", '--network', "container:$postgres", '--read-only',
        '--tmpfs', '/tmp:rw,uid=10001,gid=10001', '-e', 'AUTH_DB_HOST=localhost', '-e', 'AUTH_DB_NAME=local_auth', '-e', 'AUTH_DB_PORT=5432',
        '-e', 'AUTH_DB_SSLMODE=verify-full', '-e', 'AUTH_DB_USER=local_auth_migrator', '-e', 'AUTH_DB_PASSWORD=local-migration-only')
    $migratorCommand = @('--entrypoint', 'java', $AuthImage, '-Duser.home=/home/auth', '-cp', '/app/app.jar', 'br.com.fiap.oficina.auth.infrastructure.config.MigrateIdentity')
    $null = Invoke-Docker -Arguments ($migration + $migratorCommand) -ExpectFailure
    $null = Invoke-Docker ($migration + @('-v', "${caVolume}:/home/auth/.postgresql:ro") + $migratorCommand)
    $null = Invoke-Docker ($migration + @('-v', "${caVolume}:/home/auth/.postgresql:ro") + $migratorCommand)
    $checks += 3
    Write-Output 'PASS Auth MigrateIdentity image: PostgreSQL 17 verify-full, mounted JDBC root.crt, V1/V2 migrate/validate and repeat; missing CA rejected.'
    $null = Invoke-Docker @('exec', '-e', 'PGPASSWORD=local-migration-only', $postgres, 'psql', '-h', '127.0.0.1',
        '-U', 'local_auth_migrator', '-d', 'local_auth', '-c', 'CREATE TABLE fixture(id integer PRIMARY KEY); INSERT INTO fixture VALUES (1)')
    $authSql = @('exec', '-e', "PGPASSWORD=local-auth-'quoted", $postgres, 'psql', '-XAt', '-h', '127.0.0.1', '-U', 'local_auth', '-d', 'local_auth', '-c')
    Assert-Value (Invoke-Docker ($authSql + 'BEGIN; SELECT id FROM fixture FOR UPDATE; ROLLBACK')) "BEGIN`n1`nROLLBACK"
    $script:stage = 'deny runtime DDL'
    $null = Invoke-Docker -Arguments ($authSql + 'CREATE TABLE forbidden(id integer)') -ExpectFailure
    $script:stage = 'deny cross database connection'
    $null = Invoke-Docker -Arguments @('exec', '-e', "PGPASSWORD=local-auth-'quoted", $postgres, 'psql', '-h', '127.0.0.1', '-U', 'local_auth', '-d', 'local_os', '-c', 'SELECT 1') -ExpectFailure
    $thirdOutput = Invoke-Docker $bootstrap
    $script:stage = 'reject privileged existing runtime role'
    $null = Invoke-Docker @('exec', $postgres, 'psql', '-U', 'postgres', '-c', 'ALTER ROLE local_os CREATEDB')
    $null = Invoke-Docker -Arguments $bootstrap -ExpectFailure
    $null = Invoke-Docker @('exec', $postgres, 'psql', '-U', 'postgres', '-c', 'ALTER ROLE local_os NOCREATEDB')
    $null = Invoke-Docker $bootstrap
    Assert-Value (Invoke-Docker ($authSql + 'SELECT count(*) FROM fixture')) '1'
    Assert-Value (Invoke-Docker @('exec', $postgres, 'psql', '-XAt', '-U', 'postgres', '-c', "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid WHERE r.rolname LIKE 'local_%'")) '0'
    $script:stage = 'deny wrong database password'
    $null = Invoke-Docker -Arguments @('exec', '-e', 'PGPASSWORD=wrong-local-password', $postgres, 'psql', '-h', '127.0.0.1', '-U', 'local_auth', '-d', 'local_auth', '-c', 'SELECT 1') -ExpectFailure
    $databaseLogs = Invoke-Docker @('logs', $postgres)
    foreach ($secret in @("local-auth-'quoted", 'local-admin-only', 'local-migration-only', 'local-os-only', 'local-billing-only')) {
        if ((@($failureOutput, $firstOutput, $secondOutput, $thirdOutput, $databaseLogs) -join "`n").Contains($secret)) { throw 'Fixture password leaked into PostgreSQL output' }
    }
    $checks += 5
    Write-Output 'PASS PostgreSQL 17: bootstrap, repeat, unexpected owner, recovery, DML/row locks, denied DDL/cross-database/wrong password, preserved data and no passwords in logs.'

    $script:stage = 'mongo initialization'
    $mongo = "$testOwner-mongo"
    $mongoData = "$testOwner-mongo-data"
    $mongoKey = "$testOwner-mongo-key"
    foreach ($volume in @($mongoData, $mongoKey)) {
        $null = Invoke-Docker @('volume', 'create', '--label', "oficina.test-owner=$testOwner", $volume)
        $volumes += $volume
    }
    $null = Invoke-Docker @('run', '--rm', '--label', "oficina.test-owner=$testOwner", '-v', "${mongoKey}:/keyfile", $MongoImage,
        'sh', '-ec', 'printf localFixtureKeyfileOnly012345678901234567890123456789 > /keyfile/keyfile; chown 999:999 /keyfile/keyfile; chmod 400 /keyfile/keyfile')
    $containers += $mongo
    $null = Invoke-Docker @('run', '-d', '--name', $mongo, '--label', "oficina.test-owner=$testOwner", '--user', '999:999', '--read-only',
        '--tmpfs', '/tmp:rw,uid=999,gid=999', '-v', "${mongoData}:/data/db", '-v', "${mongoKey}:/keyfile:ro", '-v', "${scripts}:/scripts:ro", '-v', "${testScripts}:/tests:ro",
        '-e', 'HOME=/tmp', '-e', 'MONGO_ROOT_USERNAME=local_root', '-e', 'MONGO_ROOT_PASSWORD=local-root-only',
        '-e', 'MONGO_USERNAME=local_execution', '-e', 'MONGO_PASSWORD=local-execution-only', '-e', 'MONGO_DATABASE=oficina_execution',
        '-e', 'MONGO_MEMBER=localhost:27017', $MongoImage, 'mongod', '--replSet', 'rs0', '--auth', '--keyFile', '/keyfile/keyfile', '--bind_ip_all', '--wiredTigerCacheSizeGB', '0.25')
    Wait-Container @('exec', $mongo, 'mongosh', '--quiet', '/scripts/mongo-init.js')
    Wait-Container @('exec', $mongo, 'mongosh', '--quiet', '/scripts/mongo-ready.js')
    $null = Invoke-Docker @('exec', '-e', 'LOCAL_MONGO_PHASE=transaction', $mongo, 'mongosh', '--quiet', '/tests/local-mongo.js')
    $null = Invoke-Docker @('restart', $mongo)
    Wait-Container @('exec', $mongo, 'mongosh', '--quiet', '/scripts/mongo-ready.js')
    $null = Invoke-Docker @('exec', $mongo, 'mongosh', '--quiet', '/scripts/mongo-init.js')
    $null = Invoke-Docker @('exec', '-e', 'LOCAL_MONGO_PHASE=verify', $mongo, 'mongosh', '--quiet', '/tests/local-mongo.js')
    $null = Invoke-Docker -Arguments @('exec', '-e', 'LOCAL_MONGO_PHASE=anonymous', $mongo, 'mongosh', '--quiet', '/tests/local-mongo.js') -ExpectFailure
    $checks += 4
    Write-Output 'PASS MongoDB: authenticated rs0 PRIMARY, transaction commit, reinitialization, restart persistence and denied anonymous access.'

    $script:stage = 'rabbit authentication and persistence'
    $rabbit = "$testOwner-rabbit"
    $rabbitData = "$testOwner-rabbit-data"
    $null = Invoke-Docker @('volume', 'create', '--label', "oficina.test-owner=$testOwner", $rabbitData)
    $volumes += $rabbitData
    $null = Invoke-Docker @('run', '--rm', '--user', '0:0', '--label', "oficina.test-owner=$testOwner", '-v', "${rabbitData}:/var/lib/rabbitmq",
        '--entrypoint', 'sh', $RabbitImage, '-ec', 'printf localFixtureCookieOnly0123456789 > /var/lib/rabbitmq/.erlang.cookie; chmod 600 /var/lib/rabbitmq/.erlang.cookie; chown -R 999:999 /var/lib/rabbitmq')
    $containers += $rabbit
    $null = Invoke-Docker @('run', '-d', '--name', $rabbit, '--hostname', 'local-rabbit', '--label', "oficina.test-owner=$testOwner",
        '--user', '999:999', '--read-only', '--tmpfs', '/tmp:rw,uid=999,gid=999', '--entrypoint', 'rabbitmq-server',
        '-v', "${rabbitData}:/var/lib/rabbitmq", '-e', 'RABBITMQ_LOGS=-', '-e', 'RABBITMQ_DEFAULT_USER=local_app', '-e', 'RABBITMQ_DEFAULT_PASS=local-rabbit-only',
        '-e', 'RABBITMQ_DEFAULT_VHOST=/oficina', '-e', 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS=+S 2:2 +SDcpu 1 +SDio 1', $RabbitImage)
    Wait-Container @('exec', $rabbit, 'rabbitmq-diagnostics', '-q', 'check_running')
    $null = Invoke-Docker @('exec', $rabbit, 'rabbitmqctl', 'authenticate_user', 'local_app', 'local-rabbit-only')
    $null = Invoke-Docker -Arguments @('exec', $rabbit, 'rabbitmqctl', 'authenticate_user', 'local_app', 'wrong-local-password') -ExpectFailure
    $client = "$testOwner-amqp"
    $network = "$testOwner-network"
    $null = Invoke-Docker @('network', 'create', '--label', "oficina.test-owner=$testOwner", $network)
    $null = Invoke-Docker @('network', 'connect', $network, $rabbit)
    $containers += $client
    $null = Invoke-Docker @('run', '-d', '--name', $client, '--label', "oficina.test-owner=$testOwner", '--network', $network, '-e', "RABBIT_HOST=$rabbit",
        '-v', "${testScripts}:/tests:ro", 'python:3.13-alpine', 'sh', '-ec', 'pip install --quiet --disable-pip-version-check pika==1.3.2 && touch /tmp/ready && sleep 600')
    Wait-Container @('exec', $client, 'test', '-f', '/tmp/ready')
    $script:stage = 'AMQP publish durable message'
    $null = Invoke-Docker @('exec', $client, 'python', '/tests/local-rabbit.py', 'publish')
    $null = Invoke-Docker @('restart', $rabbit)
    Wait-Container @('exec', $rabbit, 'rabbitmq-diagnostics', '-q', 'check_running')
    $script:stage = 'AMQP reconnect and consume after restart'
    $null = Invoke-Docker @('exec', $client, 'python', '/tests/local-rabbit.py', 'consume')
    $checks += 4
    Write-Output 'PASS RabbitMQ: authentication, durable exchange/queue, persistent AMQP message, reconnect and consumption after restart.'
    Write-Output "$checks local checks passed; owner $testOwner. No AWS or Kubernetes calls."
}
finally {
    $ErrorActionPreference = 'Continue'
    foreach ($container in $containers) {
        $details = & $Docker inspect $container 2>$null
        if ($LASTEXITCODE -eq 0 -and (($details | ConvertFrom-Json)[0].Config.Labels.'oficina.test-owner') -eq $testOwner) { $null = & $Docker rm -f $container 2>&1 }
    }
    foreach ($volume in $volumes) {
        $details = & $Docker volume inspect $volume 2>$null
        if ($LASTEXITCODE -eq 0 -and (($details | ConvertFrom-Json)[0].Labels.'oficina.test-owner') -eq $testOwner) { $null = & $Docker volume rm $volume 2>&1 }
    }
    if ($null -ne $network) {
        $details = & $Docker network inspect $network 2>$null
        if ($LASTEXITCODE -eq 0 -and (($details | ConvertFrom-Json)[0].Labels.'oficina.test-owner') -eq $testOwner) { $null = & $Docker network rm $network 2>&1 }
    }
}
