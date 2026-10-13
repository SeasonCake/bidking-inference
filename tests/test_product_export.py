"""Falsifying controls for the public product inventory checker."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from scripts.verify_product import content_errors, product_errors

class ProductExport(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='public-product-check-')
        self.root=Path(self.temp.name)
        (self.root/'product').mkdir()
        self.write('value = 4\n')
    def tearDown(self): self.temp.cleanup()
    def write(self,text):
        data=text.encode()
        (self.root/'product/demo.py').write_bytes(data)
        self.manifest={'schema_version':1,'files':[{'path':'demo.py','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}
        self.save()
    def save(self): (self.root/'product/SOURCE_MANIFEST.json').write_text(json.dumps(self.manifest))
    def test_known_good(self): self.assertEqual(product_errors(self.root),[])
    def test_changed_and_unlisted_bytes(self):
        (self.root/'product/demo.py').write_text('value = 5\n')
        (self.root/'product/extra.py').write_text('value = 2\n')
        errors=product_errors(self.root)
        self.assertTrue(any('identity changed' in x for x in errors))
        self.assertTrue(any('unlisted' in x for x in errors))
    def test_traversal_rejected(self):
        self.manifest['files'][0]['path']='../outside.py';self.save()
        self.assertIn('product manifest path invalid',product_errors(self.root))
    def test_pinned_invalid_python_is_rejected(self):
        self.write('def (\n')
        self.assertTrue(any('Python syntax' in x for x in product_errors(self.root)))
    def test_secret_shape_is_reported_without_value(self):
        fake='ghp_'+'a'*36
        errors=content_errors('demo.py',('token = '+repr(fake)).encode())
        self.assertEqual(errors,['provider_token: demo.py'])
        self.assertNotIn(fake,' '.join(errors))
    def test_boolean_schema_is_not_version_one(self):
        self.manifest['schema_version']=True;self.save()
        self.assertIn('product source manifest schema invalid',product_errors(self.root))
    def test_runtime_logs_are_not_source_even_when_pinned(self):
        relative='data/logs/live/upload_status.txt'
        data=b'synthetic generated status\n'
        path=self.root/'product'/relative
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        self.manifest['files'].append({'path':relative,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        self.save()
        self.assertIn('runtime data is not source: '+relative,product_errors(self.root))
    def test_unreviewed_ip_endpoint_requires_adaptation(self):
        errors=content_errors('demo.py',b'url="https://8.8.8.8/example"\n')
        self.assertIn('unreviewed public IP endpoint: demo.py',errors)
        self.assertEqual(content_errors('demo.py',b'url="http://203.0.113.10/example"\n'),[])
        self.assertEqual(content_errors('demo.py',b'url="http://127.0.0.1/example"\n'),[])
        diagnostic='scripts/activation_transport_diagnostic.py'
        self.assertEqual(content_errors(diagnostic,b'url="https://1.1.1.1/dns-query"\n'),[])
        self.assertTrue(content_errors(diagnostic,b'url="https://1.1.1.1/different"\n'))
