import io
import json
import tempfile
import unittest

from runtime import ACCOUNT, PREFIX, Driver, Failure, Report, digest
from live_release import cleanup_release, readback_image


class ReleaseTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.driver = Driver(dict(backend_bucket='unused-offline'), Report('release', self.temporary.name))
        self.addCleanup(self.driver.temporary.cleanup)
        self.driver.save = lambda: None

    def test_ecr_readback_checks_manifest_and_image_identity(self):
        manifest = json.dumps(dict(config=dict(digest='sha256:' + 'a' * 64)))
        self.driver.api = lambda *args, **kwargs: dict(images=[dict(imageManifest=manifest, imageId=dict(imageDigest='sha256:' + digest(manifest.encode())))])
        reference = readback_image(self.driver, 'auth', 'b' * 40, 'sha256:' + 'a' * 64)
        self.assertIn(PREFIX + '-auth@sha256:', reference)
        with self.assertRaisesRegex(Failure, 'IMMUTABLE_IMAGE_CONTENT_MISMATCH'):
            readback_image(self.driver, 'auth', 'b' * 40, 'sha256:' + 'c' * 64)

    def test_pending_uploads_from_multiple_failed_revisions_are_recovered(self):
        pending = [dict(key='auth/' + revision * 40 + '/auth.jar', sha256=digest(revision.encode()), revision=revision * 40) for revision in ['a', 'b']]
        self.driver.journal['pending_jars'] = pending
        self.driver.items = lambda service, operation, result, **kwargs: [dict(Key=kwargs['Prefix'], VersionId='version-' + kwargs['Prefix'][5])]
        deleted = []

        def api(service, operation, **kwargs):
            if service == 'ecr':
                raise Failure('RepositoryNotFoundException')
            if operation == 'get_object':
                return dict(Body=io.BytesIO(kwargs['Key'][5].encode()))
            if operation == 'delete_object':
                deleted.append(kwargs)
                return {}
            self.fail('Unexpected operation: ' + operation)

        self.driver.api = api
        cleanup_release(self.driver)
        self.assertEqual(len(deleted), 2)
        self.assertTrue(all(entry['ExpectedBucketOwner'] == ACCOUNT and entry['VersionId'] for entry in deleted))
        self.assertEqual(len(self.driver.journal['objects']), 2)

    def test_absence_checks_never_treat_access_denied_as_deleted(self):
        self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('AccessDenied'))
        with self.assertRaisesRegex(Failure, 'AccessDenied'):
            self.driver.require_absent('s3', 'head_object', {'404'}, 'RESIDUAL')
        self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('404'))
        self.driver.require_absent('s3', 'head_object', {'404'}, 'RESIDUAL')


if __name__ == '__main__':
    unittest.main()
