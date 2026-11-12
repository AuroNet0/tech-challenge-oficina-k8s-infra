import ast
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import uuid

from runtime import ACCOUNT, REGION, ROOT, Failure, canonical, require


def helpers(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    names = {'until', 'required', 'wait_api', 'login', 'check'}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    require({node.name for node in functions} == names, 'BILLING_E2E_CONTRACT_CHANGED')
    return compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec')


def validate_fixture(fixture):
    require(fixture.get('environment') == 'demo-a' and fixture.get('payment_mode') == 'mercado-pago-test',
        'AWS_E2E_FIXTURE_SCOPE_REJECTED')
    require(set(fixture.get('users', {})) == {'gerente', 'mecanico', 'clienteB', 'atendente'}, 'DEMO_USERS_REQUIRED')
    require(all(re.fullmatch(r'[a-z0-9.+_-]+@example\.invalid', user.get('email', ''))
        and 12 <= len(user.get('password', '')) <= 1024 for user in fixture['users'].values()), 'DEMO_CREDENTIALS_REQUIRED')
    require(re.fullmatch(r'[0-9]{11}|[0-9]{14}', fixture.get('customerDocument', ''))
        and re.fullmatch(r'[A-Z]{3}[0-9][A-Z0-9][0-9]{2}', fixture.get('vehiclePlate', '')), 'DEMO_CUSTOMER_AND_VEHICLE_REQUIRED')


def bdd_contract(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ('SCENARIO', 'FEATURE_STEPS')}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'bdd_then']
    require(set(constants) == {'SCENARIO', 'FEATURE_STEPS'} and len(functions) == 1, 'BILLING_E2E_BDD_CONTRACT_CHANGED')
    feature = [line.strip() for line in path.with_name('oficina.feature').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    expected = ['# language: pt', 'Funcionalidade: Fluxo distribuído da oficina', 'Cenário: ' + constants['SCENARIO']] + constants['FEATURE_STEPS']
    require(feature == expected, 'BILLING_E2E_FEATURE_CHANGED')
    return constants, compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec')


def verify_provider(driver):
    secret = driver.config['mercado_pago_test_secret_arn']
    settings = json.loads(driver.api('secretsmanager', 'get_secret_value', SecretId=secret)['SecretString'])
    require(settings.get('access-token', '').startswith('TEST-') and settings.get('webhook-secret'),
        'MERCADO_PAGO_TEST_CREDENTIAL_REQUIRED')
    request = urllib.request.Request('https://api.mercadopago.com/users/me',
        headers={'Authorization': 'Bearer ' + settings['access-token']})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            account = json.load(response)
    except urllib.error.HTTPError as error:
        raise Failure('MERCADO_PAGO_API_REJECTED', 'GET /users/me', dict(http_status=error.code,
            correction='Confira a credencial TEST e o vendedor de teste; o corpo da resposta foi omitido.')) from None
    require('test_user' in account.get('tags', []), 'MERCADO_PAGO_TEST_ACCOUNT_NOT_PROVEN')
    driver.receipt('mercado-pago', dict(verified=True, test_account=True,
        transaction_verified=False, proof='GET /users/me; test_user tag; TEST credential'))


def run(driver):
    endpoint = driver.outputs('edge')['edge_contract']['api_endpoint']
    require(re.fullmatch(r'https://[a-z0-9]+\.execute-api\.us-east-1\.amazonaws.com', endpoint), 'AWS_E2E_ENDPOINT_REJECTED')
    secret = os.environ['E2E_FIXTURE_SECRET_ARN']
    require(re.fullmatch(fr'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:/oficina/phase4/demo-a/inputs/e2e-fixture-[A-Za-z0-9]{{6}}', secret),
        'AWS_E2E_SECRET_SCOPE_REJECTED')
    fixture = json.loads(driver.api('secretsmanager', 'get_secret_value', SecretId=secret)['SecretString'])
    validate_fixture(fixture)
    namespace = dict(time=time, CONFIG=fixture, TOKENS={}, CHECKS=[])
    trace = []

    def http(service, method, path, actor=None, body=None, **kwargs):
        require(service in ('auth', 'os', 'execution', 'billing') and path.startswith('/api/v1/'), 'AWS_E2E_REQUEST_REJECTED')
        headers = {'Content-Type': 'application/json'}
        if actor:
            headers['Authorization'] = 'Bearer ' + namespace['TOKENS'][actor]
        request = urllib.request.Request(endpoint + path, method=method, headers=headers,
            data=None if body is None else canonical(body))
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, content = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, content = error.code, error.read()
        except (urllib.error.URLError, TimeoutError):
            trace.append(dict(service=service, method=method, path=path.split('?')[0], status=0))
            raise Failure('AWS_E2E_NETWORK_FAILED', endpoint + path.split('?')[0]) from None
        trace.append(dict(service=service, method=method, path=path.split('?')[0], status=status))
        return status, json.loads(content) if content else None

    namespace['http'] = http
    exec(helpers(ROOT / 'sources/billing/integration/9.1.4/runner.py'), namespace)
    bdd, assertion = bdd_contract(ROOT / 'sources/billing/integration/9.1.4/runner.py')
    original_until = namespace['until']
    namespace['until'] = lambda name, operation, timeout=150: original_until(name, operation,
        900 if '/pagamentos/' in name else timeout)
    required, wait_api = namespace['required'], namespace['wait_api']
    evidence = dict(payment_mode='mercado-pago-test', fixture_provenance='Seeded fictitious users and scoped grants; real HTTP operations and deployed databases',
        endpoint=endpoint, checks=namespace['CHECKS'], trace=trace)
    try:
        from demo_fixture import assignment_sql, branch_sql, execute_sql, identifier, identity_sql, literal
        execute_sql(driver, 'auth', identity_sql(fixture), 'identity')
        execute_sql(driver, 'os', branch_sql(fixture), 'branch')
        namespace['login']()
        branch = identifier('demo-branch')
        customers = required('os', 'GET', '/api/v1/clientes', 'atendente')
        customer = next((value for value in customers if value.get('cpfCnpj') == fixture['customerDocument']), None)
        if customer is None:
            customer = required('os', 'POST', '/api/v1/clientes?filialId=' + branch, 'atendente',
                dict(nome='Cliente Demo FIAP', cpfCnpj=fixture['customerDocument'], email=fixture['users']['clienteB']['email']))
        vehicles = required('os', 'GET', '/api/v1/veiculos?clienteId=' + customer['id'], 'atendente')
        vehicle = next((value for value in vehicles if value.get('placa') == fixture['vehiclePlate']), None)
        if vehicle is None:
            vehicle = required('os', 'POST', '/api/v1/veiculos?filialId=' + branch, 'atendente',
                dict(clienteId=customer['id'], placa=fixture['vehiclePlate'], marca='Demo', modelo='FIAP', ano=2025))
        user_id = identifier(fixture['users']['clienteB']['email'])
        execute_sql(driver, 'auth', 'INSERT INTO usuario_clientes(usuario_id,cliente_id,criado_em) VALUES ('
            + literal(user_id) + ',' + literal(str(uuid.UUID(customer['id']))) + ',now()) ON CONFLICT DO NOTHING;', 'link')
        previous = driver.journal.get('demo_order')
        if previous:
            order = required('os', 'GET', '/api/v1/ordens-servico/' + previous['id'], 'atendente')
        else:
            order = required('os', 'POST', '/api/v1/ordens-servico', 'atendente',
                dict(clienteId=customer['id'], veiculoId=vehicle['id'], filialId=branch, observacoes='Demo FIAP AWS'))
            driver.journal['demo_order'] = dict(id=order['id'], revision=os.environ['GITHUB_SHA'])
            driver.save()
        order_id = order['id']
        execute_sql(driver, 'os', assignment_sql(fixture, order_id), 'assignment')
        order_path = '/api/v1/ordens-servico/' + order_id
        require(http('os', 'GET', order_path)[0] == 401, 'UNAUTHENTICATED_ORDER_ACCEPTED')
        saga = required('os', 'GET', order_path + '/saga', 'gerente')
        diagnostic = wait_api('execution', '/api/v1/diagnosticos?osId=' + order_id, 'mecanico')
        diagnostic_path = '/api/v1/diagnosticos/' + diagnostic['id']
        require(http('execution', 'POST', diagnostic_path + '/inicio', 'atendente')[0] == 403, 'UNAUTHORIZED_DIAGNOSTIC_ACCEPTED')
        if diagnostic['status'] == 'PENDING':
            diagnostic = required('execution', 'POST', diagnostic_path + '/inicio', 'mecanico')
        if diagnostic['status'] == 'IN_PROGRESS':
            required('execution', 'POST', diagnostic_path + '/itens', 'mecanico',
                dict(itemId='demo-service', descricao='Servico de demonstracao', tipo='SERVICO', quantidade='1', unidadePreco='HORA', precoPrevisto='1.00'))
            required('execution', 'POST', diagnostic_path + '/conclusao', 'mecanico')
        quote = wait_api('billing', '/api/v1/orcamentos?osId=' + order_id, 'clienteB')
        notifications = wait_api('billing', '/api/v1/notificacoes?osId=' + order_id, 'clienteB',
            lambda values: any(value['osId'] == order_id and value['orcamentoId'] == quote['id'] for value in values))
        if quote['status'] == 'PENDING_APPROVAL':
            wait_api('os', order_path + '/saga', 'gerente', lambda value: value['status'] == 'WAITING_APPROVAL')
            required('billing', 'POST', '/api/v1/orcamentos/' + quote['id'] + '/aprovar', 'clienteB', {})
        repair = wait_api('execution', '/api/v1/reparos?osId=' + order_id, 'mecanico')
        repair_path = '/api/v1/reparos/' + repair['id']
        if repair['status'] == 'PENDING':
            repair = required('execution', 'POST', repair_path + '/inicio', 'mecanico')
        if repair['status'] == 'IN_PROGRESS':
            required('execution', 'POST', repair_path + '/conclusao', 'mecanico')
        payments = wait_api('billing', '/api/v1/pagamentos?osId=' + order_id, 'clienteB', lambda value: len(value) == 1)
        payment_path = '/api/v1/pagamentos/' + payments[0]['id']
        checkout = required('billing', 'POST', payment_path + '/checkout', 'clienteB', {})
        require(checkout['checkoutStatus'] == 'READY', 'MERCADO_PAGO_TEST_CHECKOUT_NOT_READY')
        evidence['checkout'] = dict(status=checkout['checkoutStatus'], payment_id=payments[0]['id'])
        evidence['order_id'] = order_id
        (driver.report.directory / 'aws-e2e.json').write_bytes(canonical(evidence))
        driver.report.data['endpoints'] = dict(api=endpoint, payment=endpoint + payment_path)
        print('Checkout de teste pronto. Consulte a URL com login de cliente em ' + endpoint + payment_path
            + '. Aguardando confirmação real por até 15 minutos.', flush=True)
        driver.report.data['checks'].append(dict(phase='e2e-payment', resource=payments[0]['id'], status='DEFERRED',
            reason='COMPLETE_TEST_CHECKOUT_IN_MERCADO_PAGO; WAITING_FOR_REAL_PROVIDER_CONFIRMATION', duration_ms=0))
        payment = wait_api('billing', payment_path, 'clienteB', lambda value: value['status'] == 'CONFIRMED')
        order = wait_api('os', order_path, 'clienteB', lambda value: value['status'] == 'FINALIZADA')
        saga = wait_api('os', order_path + '/saga', 'gerente', lambda value: value['status'] == 'COMPLETED')
        evidence['final_states'] = dict(payment=payment['status'], order=order['status'], saga=saga['status'])
        runtime = Path(driver.temporary.name) / 'bdd-runtime'
        runtime.mkdir(exist_ok=True)
        (runtime / 'state.json').write_bytes(canonical(dict(osMain=order_id, paymentId=payments[0]['id'])))
        namespace.update(Path=Path, RUNTIME=runtime, RESULT={})
        exec(assertion, namespace)
        namespace['bdd_then']()
        (driver.report.directory / 'aws-bdd.json').write_bytes(canonical(dict(status='PASSED', scenario=bdd['SCENARIO'],
            steps=[dict(text=step, status='PASSED') for step in bdd['FEATURE_STEPS']], final_states=evidence['final_states'],
            payment_mode='mercado-pago-test', assertions_reused='Billing integration/9.1.4 bdd_then')))
        compensation = driver.journal.get('demo_compensation_order')
        if not compensation:
            compensation = required('os', 'POST', '/api/v1/ordens-servico', 'atendente',
                dict(clienteId=customer['id'], veiculoId=vehicle['id'], filialId=branch, observacoes='Compensacao Demo FIAP AWS'))
            driver.journal['demo_compensation_order'] = dict(id=compensation['id'])
            driver.save()
        compensation_id = compensation['id']
        execute_sql(driver, 'os', assignment_sql(fixture, compensation_id), 'compensation-assignment')
        diagnostic = wait_api('execution', '/api/v1/diagnosticos?osId=' + compensation_id, 'mecanico')
        path = '/api/v1/diagnosticos/' + diagnostic['id']
        if diagnostic['status'] == 'PENDING':
            diagnostic = required('execution', 'POST', path + '/inicio', 'mecanico')
        if diagnostic['status'] == 'IN_PROGRESS':
            required('execution', 'POST', path + '/itens', 'mecanico',
                dict(itemId='demo-rejected', descricao='Orcamento de teste rejeitado', tipo='SERVICO', quantidade='1', unidadePreco='HORA', precoPrevisto='1.00'))
            required('execution', 'POST', path + '/conclusao', 'mecanico')
        quote = wait_api('billing', '/api/v1/orcamentos?osId=' + compensation_id, 'clienteB')
        if quote['status'] == 'PENDING_APPROVAL':
            required('billing', 'POST', '/api/v1/orcamentos/' + quote['id'] + '/rejeitar', 'clienteB', {})
        compensated = wait_api('os', '/api/v1/ordens-servico/' + compensation_id + '/saga', 'gerente',
            lambda value: value['status'] == 'COMPENSATED')
        cancelled = wait_api('os', '/api/v1/ordens-servico/' + compensation_id, 'clienteB', lambda value: value['status'] == 'CANCELADA')
        require(required('billing', 'GET', '/api/v1/pagamentos?osId=' + compensation_id, 'clienteB') == [], 'COMPENSATION_CREATED_PAYMENT')
        evidence['compensation'] = dict(order=cancelled['status'], saga=compensated['status'], order_id=compensation_id, no_payment=True)
        driver.receipt('compensation', dict(verified=True, **evidence['compensation']))
        driver.receipt('e2e', dict(verified=True, payment_mode='mercado-pago-test', final_states=evidence['final_states']))
        driver.report.data['environment_ready'] = True
    except TimeoutError:
        raise Failure('AWS_E2E_TRANSITION_TIMEOUT', evidence.get('checkout', {}).get('payment_id', 'Saga'),
            dict(correction='Verifique RabbitMQ/Outbox e credenciais sandbox. O checkout precisa de pagamento de teste real; nenhum pagamento foi simulado.')) from None
    finally:
        (driver.report.directory / 'aws-e2e.json').write_bytes(canonical(evidence))
