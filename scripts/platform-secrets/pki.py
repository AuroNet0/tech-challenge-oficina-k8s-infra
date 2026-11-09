import base64
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


class InvalidMaterial(ValueError):
    pass


def require(condition):
    if not condition:
        raise InvalidMaterial('PKI validation failed')


def encryption(password):
    return serialization.BestAvailableEncryption(password.encode('ascii'))


def create_ca(environment, password, lifetime_hours=48, now=None):
    require(environment in ('demo-a', 'demo-b') and 1 <= lifetime_hours <= 72)
    now = now or datetime.now(timezone.utc)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'oficina-phase4-{environment}-ca')])
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=lifetime_hours))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
                   .sign(private_key, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b'environment-ca', private_key, certificate, None, encryption(password))


def issue_identity(ca_bytes, ca_password, service, namespace, password, public_roots, now=None):
    require(service in ('os-service', 'execution-service', 'billing-service', 'auth-internal'))
    now = now or datetime.now(timezone.utc)
    ca_key, ca_certificate, _ = pkcs12.load_key_and_certificates(ca_bytes, ca_password.encode('ascii'))
    require(ca_certificate.not_valid_after_utc > now + timedelta(hours=1))
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, service)])
    usages = [ExtendedKeyUsageOID.SERVER_AUTH]
    if service != 'auth-internal':
        usages.append(ExtendedKeyUsageOID.CLIENT_AUTH)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(ca_certificate.subject)
                   .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(ca_certificate.not_valid_after_utc)
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName(service), x509.DNSName(f'{service}.{namespace}.svc.cluster.local')]), critical=False)
                   .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, None, None), critical=True)
                   .add_extension(x509.ExtendedKeyUsage(usages), critical=False)
                   .sign(ca_key, hashes.SHA256()))
    identity = pkcs12.serialize_key_and_certificates(service.encode(), private_key, certificate, [ca_certificate], encryption(password))
    return compose_identity({'identity.p12': base64.b64encode(identity).decode(), 'password': password}, ca_certificate, service, public_roots)


def compose_identity(canonical, ca_certificate, service, public_roots):
    trust = [ca_certificate] if service == 'auth-internal' else [ca_certificate, *public_roots]
    require(service == 'auth-internal' or len(public_roots) > 0)
    password = canonical['password']
    truststore = pkcs12.serialize_java_truststore([pkcs12.PKCS12Certificate(root, f'root-{index}'.encode())
                                                for index, root in enumerate(trust)], encryption(password))
    payload = dict(canonical, **{'truststore.p12': base64.b64encode(truststore).decode()})
    if service != 'auth-internal':
        inbound = pkcs12.serialize_java_truststore([pkcs12.PKCS12Certificate(ca_certificate, b'environment-ca')], encryption(password))
        payload['inbound-truststore.p12'] = base64.b64encode(inbound).decode()
        payload['java-options'] = '\n'.join([
            '-Djavax.net.ssl.keyStore=/tls/identity.p12', f'-Djavax.net.ssl.keyStorePassword={password}',
            '-Djavax.net.ssl.trustStore=/tls/truststore.p12', f'-Djavax.net.ssl.trustStorePassword={password}',
        ]) + '\n'
    return payload


def validate_identity(payload, environment, service, namespace, expected_ca, public_roots, now=None):
    try:
        now = now or datetime.now(timezone.utc)
        password = payload['password'].encode('ascii')
        if service != 'auth-internal':
            inbound = pkcs12.load_pkcs12(base64.b64decode(payload['inbound-truststore.p12'], validate=True), password)
            require(inbound.key is None and inbound.cert is None and len(inbound.additional_certs) == 1)
            require(inbound.additional_certs[0].certificate.fingerprint(hashes.SHA256()) == expected_ca.fingerprint(hashes.SHA256()))
            require(payload['java-options'] == '\n'.join([
                '-Djavax.net.ssl.keyStore=/tls/identity.p12', f"-Djavax.net.ssl.keyStorePassword={payload['password']}",
                '-Djavax.net.ssl.trustStore=/tls/truststore.p12', f"-Djavax.net.ssl.trustStorePassword={payload['password']}",
            ]) + '\n')
        private_key, certificate, chain = pkcs12.load_key_and_certificates(base64.b64decode(payload['identity.p12'], validate=True), password)
        trust = pkcs12.load_pkcs12(base64.b64decode(payload['truststore.p12'], validate=True), password)
        require(private_key is not None and certificate is not None and len(chain) == 1)
        require(chain[0].fingerprint(hashes.SHA256()) == expected_ca.fingerprint(hashes.SHA256()))
        require(expected_ca.subject == x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'oficina-phase4-{environment}-ca')]))
        for candidate in (certificate, expected_ca, *public_roots):
            require(candidate.not_valid_before_utc <= now < candidate.not_valid_after_utc)
        require(expected_ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        require(expected_ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign)
        require(all(root.extensions.get_extension_for_class(x509.BasicConstraints).value.ca for root in public_roots))
        require(certificate.not_valid_after_utc <= expected_ca.not_valid_after_utc)
        require(not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        require(certificate.subject == x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, service)]))
        require(certificate.issuer == expected_ca.subject)
        expected_ca.public_key().verify(certificate.signature, certificate.tbs_certificate_bytes, padding.PKCS1v15(), certificate.signature_hash_algorithm)
        expected_ca.public_key().verify(expected_ca.signature, expected_ca.tbs_certificate_bytes, padding.PKCS1v15(), expected_ca.signature_hash_algorithm)
        require(certificate.public_key().public_numbers() == private_key.public_key().public_numbers())
        require(set(certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
                == {service, f'{service}.{namespace}.svc.cluster.local'})
        expected_eku = {ExtendedKeyUsageOID.SERVER_AUTH}
        if service != 'auth-internal':
            expected_eku.add(ExtendedKeyUsageOID.CLIENT_AUTH)
        require(set(certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) == expected_eku)
        require(trust.key is None and trust.cert is None)
        expected_roots = [expected_ca] if service == 'auth-internal' else [expected_ca, *public_roots]
        require({item.certificate.fingerprint(hashes.SHA256()) for item in trust.additional_certs}
                == {item.fingerprint(hashes.SHA256()) for item in expected_roots})
    except Exception:
        raise InvalidMaterial('PKI validation failed') from None
