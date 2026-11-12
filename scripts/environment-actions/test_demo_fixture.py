import re
import unittest

from aws_e2e import validate_fixture
from demo_fixture import assignment_sql, branch_sql, identifier, identity_sql, literal
from runtime import Failure


class DemoFixtureTests(unittest.TestCase):
    def fixture(self):
        return dict(environment='demo-a', payment_mode='mercado-pago-test', customerDocument='12345678909',
            vehiclePlate='EAA1A23', users={name: dict(email=name.lower() + '@example.invalid', password='offline-demo-password')
                for name in ('atendente', 'gerente', 'mecanico', 'clienteB')})

    def test_seed_is_scoped_to_demo_identities_and_uses_auth_argon2_contract(self):
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
        fixture = self.fixture()
        validate_fixture(fixture)
        sql = identity_sql(fixture)
        self.assertNotIn('offline-demo-password', sql)
        hashes = re.findall(r'\$argon2id\$v=19\$m=19456,t=2,p=1\$[A-Za-z0-9+/]{22}\$[A-Za-z0-9+/]{43}', sql)
        self.assertEqual(len(hashes), 4)
        for password_hash in hashes:
            Argon2id.verify_phc_encoded(b'offline-demo-password', password_hash)
        self.assertIn('DEMO_IDENTITY_OWNERSHIP_REJECTED', sql)
        self.assertNotIn('UPDATE usuarios', sql)
        self.assertNotIn('DELETE', sql)

    def test_real_email_and_invalid_demo_documents_are_rejected(self):
        fixture = self.fixture()
        fixture['users']['gerente']['email'] = 'user@real.example'
        with self.assertRaisesRegex(Failure, 'DEMO_CREDENTIALS_REQUIRED'):
            validate_fixture(fixture)
        fixture = self.fixture()
        fixture['vehiclePlate'] = "';DROP TABLE usuarios;"
        with self.assertRaisesRegex(Failure, 'DEMO_CUSTOMER_AND_VEHICLE_REQUIRED'):
            validate_fixture(fixture)

    def test_grants_are_branch_and_order_specific_and_have_stable_ids(self):
        fixture = self.fixture()
        sql = branch_sql(fixture)
        self.assertIn(identifier('demo-branch'), sql)
        self.assertIn('ON CONFLICT DO NOTHING', sql)
        self.assertNotIn('DELETE', sql)
        order_id = 'a' * 8 + '-aaaa-aaaa-aaaa-' + 'a' * 12
        sql = assignment_sql(fixture, order_id)
        self.assertEqual(sql.count('INSERT INTO mechanic_assignments'), 9)
        self.assertEqual(sql.count(order_id), 9)
        with self.assertRaises(ValueError):
            assignment_sql(fixture, "';DROP TABLE usuarios;")
        self.assertEqual(literal("value'quoted"), "'value''quoted'")


if __name__ == '__main__':
    unittest.main()
