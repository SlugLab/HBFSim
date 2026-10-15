import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from verify_build import verify

class BuildManifestTests(unittest.TestCase):
    def test_inputs_and_built_outputs_required(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            def put(name, content):
                p = root/name
                p.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
                return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            inputs = [put(role+'.cpp', role.encode()) for role in ('agent','provider','adapter')]
            overlay = put('overlay.json', dict(schema='hbfsim.sass_switch_overlay.v1', files=[dict(role=role, output=Path(row['path']).name, output_sha256=row['sha256']) for role,row in zip(('agent','provider','adapter'),inputs)]))
            artifacts = {r:put(r+'.so', ('binary '+r).encode()) for r in ('agent','provider','server','gate','pass','cudart12')}
            receipt = put('receipt.json', dict(status='PASS',commands=[dict(argv=['compiler','source'],returncode=0)],outputs=[artifacts[r] for r in ('agent','provider')]))
            data = dict(schema='hbfsim.sass_research_build.v1',overlay_manifest=overlay,source_inputs=inputs,build_receipts=[receipt],artifacts=artifacts)
            def check():
                manifest=put('manifest.json',data)
                return verify(Path(manifest['path']),manifest['sha256'],{r:v['path'] for r,v in artifacts.items()})
            self.assertEqual(check()['schema'],data['schema'])
            data['source_inputs']=inputs[:2]
            with self.assertRaisesRegex(ValueError,'overlay consumer'):
                check()
            data['source_inputs']=inputs
            artifacts['agent']=put('other.so',b'unbuilt')
            with self.assertRaisesRegex(ValueError,'successful output'):
                check()

class CustomAgentTests(unittest.TestCase):
    def test_custom_input_keeps_bytes_and_requires_matching_pin(self):
        from prepare_overlay import agent_input, transform
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)/'current.cpp'
            raw = b'// CUDA12 current repair and stage pins preserved\nbool qkv_enabled() { return true; }\n'
            p.write_bytes(raw)
            pin = hashlib.sha256(raw).hexdigest()
            path, actual = agent_input(Path(t), p, pin)
            self.assertEqual(actual, raw)
            self.assertEqual(path, p.resolve())
            output = transform('agent', actual.decode())
            self.assertIn('// CUDA12 current repair and stage pins preserved', output)
            self.assertEqual(p.read_bytes(), raw)
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                agent_input(Path(t), p, '0'*64)
            with self.assertRaisesRegex(ValueError, 'requires'):
                agent_input(Path(t), p)
