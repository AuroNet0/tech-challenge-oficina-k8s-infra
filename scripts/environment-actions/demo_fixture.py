import copy
import hashlib
import json
import os
import uuid

from runtime import PREFIX, canonical, cleanup_steps, require


def identifier(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, PREFIX + '/' + value))


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def identity_sql(fixture):
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    roles = dict(atendente='ATENDENTE', gerente='GERENTE', mecanico='MECANICO', clienteB='CLIENTE')
    statements = ['BEGIN;', "SELECT pg_advisory_xact_lock(hashtextextended('oficina-demo-fixture',0));"]
    for name, role in roles.items():
        user = fixture['users'][name]
        user_id = identifier(user['email'])
        password_hash = Argon2id(salt=os.urandom(16), length=32, iterations=2, lanes=1, memory_cost=19456).derive_phc_encoded(user['password'].encode())
        statements.append('INSERT INTO usuarios(id,email,nome,password_hash,ativo,criado_em,atualizado_em) VALUES ('
            + ','.join(map(literal, (user_id, user['email'], 'Demo FIAP ' + name, password_hash)))
            + ',true,now(),now()) ON CONFLICT DO NOTHING;')
        statements.append('DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM usuarios WHERE id=' + literal(user_id)
            + ' AND email=' + literal(user['email']) + " AND nome LIKE 'Demo FIAP %') THEN RAISE EXCEPTION 'DEMO_IDENTITY_OWNERSHIP_REJECTED'; END IF; END $$;")
        statements.append('INSERT INTO usuario_papeis(usuario_id,papel) VALUES (' + literal(user_id) + ',' + literal(role) + ') ON CONFLICT DO NOTHING;')
    return '\n'.join(statements + ['COMMIT;'])


def branch_sql(fixture):
    branch = identifier('demo-branch')
    actor = identifier(fixture['users']['gerente']['email'])
    statements = ['BEGIN;', 'INSERT INTO filiais(id,nome,codigo,cidade,uf,ativo,created_at,updated_at) VALUES ('
        + literal(branch) + ",'Demo FIAP','DEMO-FIAP','Sao Paulo','SP',true,now(),now()) ON CONFLICT DO NOTHING;"]
    for name, capabilities in (
        ('atendente', ('OS_READ', 'OS_OPEN', 'CLIENT_READ', 'CLIENT_CREATE', 'VEHICLE_READ', 'VEHICLE_CREATE', 'BRANCH_READ')),
        ('gerente', ('OS_READ', 'BRANCH_READ'))):
        user_id = identifier(fixture['users'][name]['email'])
        for capability in capabilities:
            grant = identifier(user_id + branch + capability)
            statements.append('INSERT INTO operational_grants(id,usuario_id,filial_id,capability,concedido_por,concedido_em) VALUES ('
                + ','.join(map(literal, (grant, user_id, branch, capability, actor))) + ',now()) ON CONFLICT DO NOTHING;')
    statements.append('INSERT INTO administrative_grants(id,usuario_id,filial_id,capability,concedido_por,concedido_em) VALUES ('
        + ','.join(map(literal, (identifier(actor + branch + 'saga'), actor, branch, 'SAGA_INSPECT', actor))) + ',now()) ON CONFLICT DO NOTHING;')
    return '\n'.join(statements + ['COMMIT;'])


def assignment_sql(fixture, order_id):
    order_id = str(uuid.UUID(order_id))
    mechanic = identifier(fixture['users']['mecanico']['email'])
    actor = identifier(fixture['users']['gerente']['email'])
    statements = ['BEGIN;']
    for capability in ('OPERATIONAL_READ', 'VEHICLE_OPERATIONAL_READ', 'DIAGNOSTIC_READ', 'DIAGNOSTIC_WRITE',
            'DIAGNOSTIC_COMPLETE', 'REPAIR_READ', 'REPAIR_WRITE', 'REPAIR_COMPLETE', 'HISTORY_READ'):
        statements.append('INSERT INTO mechanic_assignments(id,usuario_id,os_id,capability,concedido_por,concedido_em) VALUES ('
            + ','.join(map(literal, (identifier(mechanic + order_id + capability), mechanic, order_id, capability, actor)))
            + ',now()) ON CONFLICT DO NOTHING;')
    return '\n'.join(statements + ['COMMIT;'])


def execute_sql(driver, service, sql, label):
    require(service in ('auth', 'os'), 'DEMO_DATABASE_SCOPE_REJECTED')
    from kubernetes import client
    platform = driver.outputs('platform')
    infra = driver.outputs('infra')['platform_contract']
    with driver.kubernetes():
        core = client.CoreV1Api()
        secret_name = 'demo-fixture-sql'
        labels = {'oficina/environment': 'demo-a', 'oficina/managed-by': 'actions-demo-fixture'}
        metadata = dict(name=secret_name, namespace=PREFIX, labels=labels)
        secret = dict(apiVersion='v1', kind='Secret', metadata=metadata, stringData={'seed.sql': sql})
        try:
            existing = core.read_namespaced_secret(secret_name, PREFIX)
        except client.ApiException as error:
            if error.status != 404:
                raise
            core.create_namespaced_secret(PREFIX, secret)
        else:
            require(all((existing.metadata.labels or {}).get(key) == value for key, value in labels.items()), 'DEMO_SECRET_OWNERSHIP_REJECTED')
            core.replace_namespaced_secret(secret_name, PREFIX, secret)
        job = copy.deepcopy(json.loads(platform['manifests']['bootstrap'])['items'][0])
        name = 'demo-' + label + '-' + hashlib.sha256(sql.encode()).hexdigest()[:12]
        job['metadata']['name'] = name
        pod = job['spec']['template']['spec']
        container = pod['containers'][0]
        container['command'] = ['sh', '-ec', 'psql -Xq --set=ON_ERROR_STOP=1 --file=/demo/seed.sql >/dev/null']
        container['env'] = [dict(name='PGHOST', value=infra['rds']['address']), dict(name='PGPORT', value='5432'),
            dict(name='PGDATABASE', value=infra['rds']['application_databases'][service]['database']),
            dict(name='PGSSLMODE', value='verify-full'), dict(name='PGSSLROOTCERT', value='/rds-ca/ca.pem'),
            *[dict(name=name, valueFrom=dict(secretKeyRef=dict(name=service + '-postgres', key=key)))
                for name, key in (('PGUSER', 'username'), ('PGPASSWORD', 'password'))]]
        container['volumeMounts'] = [dict(name='rds-ca', mountPath='/rds-ca', readOnly=True), dict(name='demo-sql', mountPath='/demo', readOnly=True)]
        pod['volumes'] = [dict(name='rds-ca', configMap=dict(name='rds-ca')), dict(name='demo-sql', secret=dict(secretName=secret_name))]
        original_error = None
        try:
            driver.apply_bundle(dict(kind='List', items=[job]))
            driver.kubectl(['wait', 'job/' + name, '-n', PREFIX, '--for=condition=complete', '--timeout=300s'])
            observed = json.loads(driver.kubectl(['get', 'job', name, '-n', PREFIX, '-o', 'json']))
            require(observed['status'].get('succeeded', 0) == 1, 'DEMO_FIXTURE_JOB_FAILED', name)
        except BaseException as error:
            original_error = error
            raise
        finally:
            cleanup_steps(driver.report, [('demo-fixture-sql', lambda: core.delete_namespaced_secret(secret_name, PREFIX))], original_error)
