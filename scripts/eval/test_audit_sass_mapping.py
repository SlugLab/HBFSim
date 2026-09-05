"""CPU-only disassembly bundle/cache controls; fixture text is not SASS proof."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from audit_sass_mapping import ROOT, collect_mapping_artifacts


class SassBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.sass-bundle-test-',dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.cubin=self.base/'fixture.cubin';self.cubin.write_bytes(b'TEST_ONLY_CUBIN')
        self.ptx=self.base/'fixture.ptx';self.ptx.write_text('TEST_ONLY_PTX')
        self.build=self.base/'build.json'
        self.build.write_text(json.dumps(dict(schema_version=1,cubin_sha256=self.sha(self.cubin),ptx_sha256=self.sha(self.ptx))))
        self.tools=Path('/usr/local/cuda-13.0/bin')
        self.calls=[]

    def sha(self,path):return hashlib.sha256(path.read_bytes()).hexdigest()

    def fake(self,argv,stdout,stderr,timeout):
        self.calls.append(argv)
        stdout.write(b'TEST_ONLY_DISASSEMBLY\n')
        return 0

    def invoke(self):
        return collect_mapping_artifacts(self.cubin,self.ptx,self.build,self.base/'bundle',
                                         cuda_bin=self.tools,runner=self.fake)

    def test_cached_bundle_does_not_repeat_disassembly_or_claim_mapping(self):
        result=self.invoke()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(result['mapping_validation'],'NOT_PROVEN')
        self.assertEqual(result['provenance'],'MOCK')
        self.assertFalse(result['gpu_executed'])
        self.invoke()
        self.assertEqual(len(self.calls),2)

    def test_changed_artifact_or_source_cannot_reuse_bundle(self):
        self.invoke()
        (self.base/'bundle/cuobjdump.sass.txt').write_text('CHANGED')
        with self.assertRaises(ValueError):self.invoke()
        self.ptx.write_text('CHANGED_SOURCE')
        with self.assertRaises(ValueError):self.invoke()

    def test_failed_tool_never_completes_bundle(self):
        def fail(argv,stdout,stderr,timeout):
            stderr.write(b'TEST_ONLY tool failure\n')
            return 2
        with self.assertRaises(ValueError):
            collect_mapping_artifacts(self.cubin,self.ptx,self.build,self.base/'failure',cuda_bin=self.tools,runner=fail)
        self.assertEqual(json.loads((self.base/'failure/manifest.json').read_text())['collection'],'FAILED')

    def test_cache_receipt_cannot_promote_gold_or_remove_artifact_checks(self):
        original=self.invoke()
        for update in (dict(scientific_validation_passed=True),dict(provenance='PROJECTED'),dict(artifact_hashes={})):
            changed=dict(original,**update)
            (self.base/'bundle/manifest.json').write_text(json.dumps(changed))
            with self.subTest(update=update),self.assertRaises(ValueError):self.invoke()

    def test_rehashed_cached_input_cannot_differ_from_requested_input(self):
        result=self.invoke()
        cached=self.base/'bundle/kernel.cubin'
        cached.write_bytes(b'OTHER_TEST_ONLY_CUBIN')
        result['artifact_hashes']['kernel.cubin']=self.sha(cached)
        (self.base/'bundle/manifest.json').write_text(json.dumps(result))
        with self.assertRaises(ValueError):self.invoke()

    def test_extra_cached_artifacts_are_rejected(self):
        self.invoke()
        (self.base/'bundle/UNTRACKED.json').write_text('{}')
        with self.assertRaises(ValueError):self.invoke()

    def test_extra_initial_artifacts_are_rejected_before_completion(self):
        def extra(argv,stdout,stderr,timeout):
            (Path(stdout.name).parent/'UNTRACKED.json').write_text('{}')
            return self.fake(argv,stdout,stderr,timeout)
        with self.assertRaises(ValueError):
            collect_mapping_artifacts(self.cubin,self.ptx,self.build,self.base/'extra-initial',cuda_bin=self.tools,runner=extra)

    def test_disassembly_tool_change_during_collection_rejects(self):
        self.tools=self.base/'tools';self.tools.mkdir()
        for name in ('cuobjdump','nvdisasm'):(self.tools/name).write_bytes(b'TEST_ONLY_EXECUTABLE')
        def mutate(argv,stdout,stderr,timeout):
            Path(argv[0]).write_bytes(b'CHANGED_TEST_ONLY_EXECUTABLE')
            return self.fake(argv,stdout,stderr,timeout)
        with self.assertRaises(ValueError):
            collect_mapping_artifacts(self.cubin,self.ptx,self.build,self.base/'changed-tool',cuda_bin=self.tools,runner=mutate)

    def test_large_disassembly_is_valid_on_initial_and_cached_collection(self):
        def large(argv,stdout,stderr,timeout):
            self.calls.append(argv)
            stdout.write(b'TEST_ONLY\n'+b'x'*(16*1024*1024))
            return 0
        args=(self.cubin,self.ptx,self.build,self.base/'large')
        first=collect_mapping_artifacts(*args,cuda_bin=self.tools,runner=large)
        second=collect_mapping_artifacts(*args,cuda_bin=self.tools,runner=large)
        self.assertEqual(first['cache_key'],second['cache_key'])
        self.assertEqual(len(self.calls),2)


if __name__=='__main__':unittest.main()
