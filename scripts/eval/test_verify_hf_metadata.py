"""Tiny explicit HF fixtures; no real checkpoint or GPU is used."""
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import verify_hf_metadata as verifier

ROOT = Path(__file__).resolve().parents[2]


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def fixture(base, *, missing=False, overlap=False, wrong_dtype=False):
    checkpoint = base / 'checkpoint'; checkpoint.mkdir()
    config = dict(architectures=['Qwen3MoeForCausalLM'], torch_dtype='bfloat16',
                  num_hidden_layers=2, num_experts=2, num_experts_per_tok=1,
                  hidden_size=128, moe_intermediate_size=64, model_type='qwen3_moe',
                  num_attention_heads=4, num_key_value_heads=2, head_dim=32, vocab_size=16,
                  attention_bias=False, mlp_only_layers=[], decoder_sparse_step=1,
                  tie_word_embeddings=False)
    metadata = {'config.json': encoded(config), 'generation_config.json': b'{}',
                'tokenizer_config.json': b'{}', 'tokenizer.json': b'{}',
                'merges.txt': b'# fixture\n', 'vocab.json': b'{}'}
    headers = {}; locations = {}; weight_map = {}; total = 0; nonexpert = 0
    for layer in range(2):
        shard = f'model-{layer+1:05}-of-00002.safetensors'
        header = {}; cursor = 0
        for expert in range(2):
            for projection in ('gate', 'up', 'down'):
                if missing and (layer, expert, projection) == (1, 1, 'down'): continue
                name = f'model.layers.{layer}.mlp.experts.{expert}.{projection}_proj.weight'
                shape = [128, 64] if projection == 'down' else [64, 128]
                begin = 0 if overlap and layer == 1 and expert == 1 else cursor
                header[name] = dict(dtype='F16' if wrong_dtype else 'BF16', shape=shape,
                                    data_offsets=[begin, begin + 16384])
                weight_map[name] = shard; cursor += 16384
        prefix=f'model.layers.{layer}.'
        resident={prefix+'mlp.gate.weight':[2,128],prefix+'input_layernorm.weight':[128],
                  prefix+'post_attention_layernorm.weight':[128],prefix+'self_attn.q_proj.weight':[128,128],
                  prefix+'self_attn.k_proj.weight':[64,128],prefix+'self_attn.v_proj.weight':[64,128],
                  prefix+'self_attn.o_proj.weight':[128,128],prefix+'self_attn.q_norm.weight':[32],
                  prefix+'self_attn.k_norm.weight':[32]}
        if layer==0:resident.update({'model.embed_tokens.weight':[16,128], 'lm_head.weight':[16,128], 'model.norm.weight':[128]})
        for name,shape in resident.items():
            size=math.prod(shape)*2;header[name]=dict(dtype='BF16',shape=shape,data_offsets=[cursor,cursor+size])
            weight_map[name]=shard;cursor+=size;nonexpert+=size
        raw = encoded(header); prefix = struct.pack('<Q', len(raw)); headers[shard] = len(raw)+8
        path = checkpoint/shard
        with path.open('wb') as output:
            output.write(prefix+raw); output.truncate(len(raw)+8+cursor)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        for name, tensor in header.items():
            lo, hi = tensor['data_offsets']
            locations[name] = dict(tensor=name, bytes=hi-lo, dtype=tensor['dtype'],
                shape=tensor['shape'], data_offset_begin=lo, data_offset_end=hi,
                file_offset_begin=lo+len(raw)+8, file_offset_end=hi+len(raw)+8,
                source_shard=shard, source_shard_sha256=sha)
        total += cursor
    index = dict(metadata=dict(total_size=total), weight_map=weight_map)
    metadata['model.safetensors.index.json'] = encoded(index)
    for name, raw in metadata.items(): (checkpoint/name).write_bytes(raw)
    files = []
    names=['config.json','generation_config.json','tokenizer_config.json','tokenizer.json',
           'merges.txt','vocab.json','model.safetensors.index.json']+sorted(headers)
    for path in (checkpoint/name for name in names):
        st = path.stat()
        files.append(dict(path=path.name, input_path=str(path), realpath=str(path.resolve()),
                          is_symlink=False, mtime_ns=st.st_mtime_ns, size_bytes=st.st_size,
                          sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    experts = []
    for layer in range(2):
        for expert in range(2):
            names = [f'model.layers.{layer}.mlp.experts.{expert}.{p}_proj.weight'
                     for p in ('gate', 'up', 'down')]
            if any(n not in locations for n in names): continue
            gate, up, down = [locations[n] for n in names]
            experts.append(dict(layer=layer, expert_id=expert, total_bytes=49152,
                w13=dict(bytes=32768, dtype='BF16', shape_components=[[64,128],[64,128]],
                         tensors=[gate, up]),
                w2=dict(bytes=16384, dtype='BF16', shape=[128,64], tensor=down)))
    fingerprint=hashlib.sha256(encoded([{k:f[k] for k in ('path','size_bytes','sha256')} for f in files])).hexdigest()
    donor = dict(schema_version=1, ModelFingerprint=fingerprint,
        fingerprint_definition='historical fixture only', generated_at_utc='2026-01-01T00:00:00Z',
        model_input_path=str(checkpoint), model_realpath=str(checkpoint), files=files,
        architecture=config['architectures'], dtype='bfloat16',
        configuration={k:v for k,v in config.items() if k not in ('architectures','torch_dtype','model_type',
            'attention_bias','mlp_only_layers','decoder_sparse_step','tie_word_embeddings')},
        weight_map=weight_map, index_metadata=index['metadata'], expert_weights=experts,
        expert_count_total=4, expert_weight_bytes=196608, per_expert_bytes_unique=[49152],
        tensor_payload_bytes=total, non_expert_tensor_bytes=nonexpert,
        safetensors_shard_bytes=sum(f['size_bytes'] for f in files if f['path'].endswith('.safetensors')),
        shard_count=2, total_model_file_bytes=sum(f['size_bytes'] for f in files),
        has_shared_expert=False, shared_expert_tensors=[],
        tokenizer_hashes={f['path']:f['sha256'] for f in files
                          if f['path'] in ('tokenizer.json','tokenizer_config.json','merges.txt','vocab.json')})
    path = base/'donor.json'; path.write_bytes(encoded(donor))
    return checkpoint, path, headers


class MetadataRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.test-hf-metadata-', dir=ROOT)
        self.addCleanup(self.tmp.cleanup); self.base=Path(self.tmp.name)

    def run_fixture(self, **options):
        checkpoint, donor, headers = fixture(self.base, **options)
        out = self.base/'refresh'
        receipt = verifier.verify(checkpoint, donor, out, test_only=True)
        return checkpoint, donor, headers, out, receipt

    def test_complete_metadata_receipt_preserves_historical_identity_without_payload_reads(self):
        checkpoint, donor, headers = fixture(self.base)
        original = donor.read_bytes(); reads=[]; pread=os.pread
        def bounded(fd, n, offset):
            path=Path(os.readlink(f'/proc/self/fd/{fd}'))
            if path.name in headers:
                self.assertLessEqual(offset+n, headers[path.name]); reads.append((path.name,n,offset))
            return pread(fd,n,offset)
        with mock.patch.object(verifier.os, 'pread', side_effect=bounded):
            receipt=verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)
        self.assertEqual(receipt['status'],'METADATA_VERIFIED')
        self.assertEqual(receipt['provenance'],'MOCK')
        self.assertFalse(receipt['weight_payload_rehashed'])
        self.assertFalse(receipt['scientific_validation_passed'])
        self.assertEqual(receipt['historical_model_fingerprint'],json.loads(original)['ModelFingerprint'])
        self.assertEqual(receipt['summary']['expert_count'],4)
        self.assertEqual(receipt['summary']['expert_weight_bytes'],196608)
        self.assertEqual(receipt['summary']['tensor_count'],33)
        self.assertEqual((self.base/'refresh/donor.json').read_bytes(),original)
        self.assertEqual(len(reads),4)
        verifier.validate_refresh(self.base/'refresh')
        verifier.check_current_inputs(self.base/'refresh')

    def test_missing_expert_rejected_even_when_index_matches_headers(self):
        with self.assertRaises(ValueError): self.run_fixture(missing=True)

    def test_overlapping_extents_rejected_even_when_donor_agrees(self):
        with self.assertRaises(ValueError): self.run_fixture(overlap=True)

    def test_unsupported_expert_dtype_rejected_without_conversion(self):
        with self.assertRaises(ValueError): self.run_fixture(wrong_dtype=True)

    def test_changed_small_metadata_rejected(self):
        checkpoint, donor, _=fixture(self.base)
        (checkpoint/'tokenizer.json').write_bytes(b'{"changed":true}')
        with self.assertRaises(ValueError): verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)

    def test_historical_tensor_extent_corruption_rejected(self):
        checkpoint, donor, _=fixture(self.base)
        doc=json.loads(donor.read_bytes());doc['expert_weights'][0]['w2']['tensor']['file_offset_begin']+=2
        donor.write_bytes(encoded(doc))
        with self.assertRaises(ValueError): verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)

    def test_truncated_or_oversized_header_rejected_before_large_read(self):
        checkpoint, donor, _=fixture(self.base)
        shard=next(checkpoint.glob('*.safetensors'))
        with shard.open('r+b') as output: output.write(struct.pack('<Q',2**40))
        with self.assertRaises(ValueError): verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)

    def test_mutation_during_header_read_rejected(self):
        checkpoint, donor, headers=fixture(self.base); pread=os.pread; mutated=[]
        def changing(fd,n,offset):
            raw=pread(fd,n,offset); path=Path(os.readlink(f'/proc/self/fd/{fd}'))
            if path.name in headers and offset==8 and not mutated:
                st=path.stat();os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns+1));mutated.append(True)
            return raw
        with mock.patch.object(verifier.os,'pread',side_effect=changing):
            with self.assertRaises(ValueError): verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)
        self.assertTrue(mutated)

    def test_readable_checkpoint_symlinks_allowed_but_retargeting_rejected(self):
        checkpoint,donor,_=fixture(self.base);p=checkpoint/'tokenizer.json';target=self.base/'tokenizer-original'
        p.rename(target);p.symlink_to(target)
        with self.assertRaises(ValueError): verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)

    def test_frozen_validation_does_not_reopen_source_and_rejects_tampered_artifact(self):
        checkpoint,donor,_,out,_=self.run_fixture()
        checkpoint.rename(self.base/'unavailable');donor.unlink()
        verifier.validate_refresh(out)
        p=out/'metadata/config.json';p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(ValueError):verifier.validate_refresh(out)

    def test_arm_input_check_rejects_mutation_after_refresh(self):
        checkpoint,_,_,out,_=self.run_fixture()
        shard=next(checkpoint.glob('*.safetensors'));st=shard.stat()
        os.utime(shard,ns=(st.st_atime_ns,st.st_mtime_ns+1))
        with self.assertRaises(ValueError):verifier.check_current_inputs(out)

    def test_existing_destination_and_formal_tree_refused(self):
        checkpoint,donor,_,out,_=self.run_fixture()
        original=(out/'receipt.json').read_bytes()
        with self.assertRaises((ValueError,FileExistsError)):
            verifier.verify(checkpoint,donor,out,test_only=True)
        self.assertEqual((out/'receipt.json').read_bytes(),original)
        with self.assertRaises(ValueError):
            verifier.verify(checkpoint,donor,ROOT/'results/runs/hf-metadata-test-refusal',test_only=True)
        self.assertFalse((ROOT/'results/runs/hf-metadata-test-refusal').exists())

    def test_duplicate_json_and_nonfinite_values_are_refused(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{"x":1e999}', b'{"x":-1e999}'):
            with self.assertRaises(ValueError):verifier.strict_object(raw)

    def test_frozen_extra_or_link_artifacts_refused(self):
        _,_,_,out,_=self.run_fixture(); extra=out/'unexpected'
        extra.write_bytes(b'fixture')
        with self.assertRaises(ValueError):verifier.validate_refresh(out)
        extra.unlink();extra.symlink_to(out/'donor.json')
        with self.assertRaises(ValueError):verifier.validate_refresh(out)

    def test_scheduler_reserved_filenames_are_still_extra_in_metadata_bundle(self):
        _,_,_,out,_=self.run_fixture()
        for name in ('manifest.json','status.json'):
            path=out/name;path.write_bytes(b'{}')
            with self.assertRaises(ValueError):verifier.validate_refresh(out)
            path.unlink()

    def test_preexisting_frozen_readonly_symlink_target_is_supported(self):
        checkpoint,donor,_=fixture(self.base);path=checkpoint/'tokenizer.json';target=self.base/'tokenizer-blob'
        path.rename(target);path.symlink_to(target)
        document=json.loads(donor.read_bytes())
        for row in document['files']:
            if row['path']=='tokenizer.json':row.update(realpath=str(target),is_symlink=True)
        donor.write_bytes(encoded(document))
        result=verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)
        self.assertTrue(any('target' in hop for hop in result['inputs']['tokenizer.json']['chain']))
        verifier.check_current_inputs(self.base/'refresh')

    def test_payload_mutation_with_restored_mtime_is_detected_by_identity(self):
        checkpoint,_,headers,out,_=self.run_fixture();path=next(checkpoint.glob('*.safetensors'));st=path.stat()
        with path.open('r+b') as output:output.seek(headers[path.name]);output.write(b'xx')
        os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns))
        with self.assertRaises(ValueError):verifier.check_current_inputs(out)

    def test_resealed_summary_cannot_replace_tensor_semantics(self):
        _,_,_,out,receipt=self.run_fixture()
        receipt['summary']['expert_weight_bytes']+=1
        receipt['metadata_identity_sha256'],receipt['observation_identity_sha256']=verifier.identities(receipt)
        (out/'receipt.json').write_bytes(encoded(receipt))
        marker=json.loads((out/'COMPLETE.json').read_bytes())
        marker.update(receipt_sha256=hashlib.sha256((out/'receipt.json').read_bytes()).hexdigest(),
                      observation_identity_sha256=receipt['observation_identity_sha256'])
        (out/'COMPLETE.json').write_bytes(encoded(marker))
        with self.assertRaises(ValueError):verifier.validate_refresh(out)

    def test_header_size_limit_is_checked_before_reading_body(self):
        checkpoint,donor,_=fixture(self.base); path=next(checkpoint.glob('*.safetensors'))
        with path.open('r+b') as output:output.write(struct.pack('<Q',2**40))
        reads=[];pread=os.pread
        def bounded(fd,n,offset):reads.append((n,offset));return pread(fd,n,offset)
        with mock.patch.object(verifier.os,'pread',side_effect=bounded):
            with self.assertRaises(ValueError):
                verifier.snapshot(path,header=True,budget={'remaining':64<<20},limit=16<<20)
        self.assertEqual(reads,[(8,0)])

    def test_retargeted_endpoint_is_rejected_before_open_or_read(self):
        checkpoint,donor,_=fixture(self.base);path=checkpoint/'config.json';target=self.base/'unapproved-target'
        target.write_bytes(b'private fixture bytes');path.unlink();path.symlink_to(target)
        opens=[];original=os.open
        def observed(path,*args,**kwargs):
            if str(path)==str(target):opens.append(str(path))
            return original(path,*args,**kwargs)
        with mock.patch.object(verifier.os,'open',side_effect=observed):
            with self.assertRaises(ValueError):verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)
        self.assertEqual(opens,[])

    def test_receipt_changed_during_frozen_validation_is_rejected(self):
        _,_,_,out,_=self.run_fixture();snapshot=verifier.snapshot;changed=[]
        def changing(path,**kwargs):
            result=snapshot(path,**kwargs)
            if Path(path)==out/'donor.json' and not changed:
                p=out/'receipt.json';record=json.loads(p.read_bytes());record['status']='INVALID_METADATA'
                p.write_bytes(encoded(record));changed.append(True)
            return result
        with mock.patch.object(verifier,'snapshot',side_effect=changing):
            with self.assertRaises(ValueError):verifier.validate_refresh(out)
        self.assertTrue(changed)

    def test_output_cannot_enter_checkpoint_through_alias(self):
        checkpoint,donor,_=fixture(self.base);view=self.base/'checkpoint-view';view.symlink_to(checkpoint,target_is_directory=True)
        record=json.loads(donor.read_bytes());record['model_input_path']=str(view);donor.write_bytes(encoded(record))
        out=checkpoint/'forbidden-output'
        with self.assertRaises(ValueError):verifier.verify(view,donor,out,test_only=True)
        self.assertFalse(out.exists())

    def test_success_is_not_visible_before_final_bundle_validation(self):
        checkpoint,donor,_=fixture(self.base);out=self.base/'refresh';publish=verifier.atomic_json;premature=[]
        def observing(path,value):
            publish(path,value)
            if Path(path)==out/'receipt.json' and value.get('status')=='METADATA_VERIFIED':
                try:verifier.validate_refresh(out)
                except ValueError:pass
                else:premature.append(True)
        with mock.patch.object(verifier,'atomic_json',side_effect=observing):
            verifier.verify(checkpoint,donor,out,test_only=True)
        self.assertEqual(premature,[])
        verifier.validate_refresh(out)
        (out/'COMPLETE.json').unlink()
        with self.assertRaises(ValueError):verifier.validate_refresh(out)

    def test_extra_artifact_created_during_validation_is_rejected(self):
        _,_,_,out,_=self.run_fixture();snapshot=verifier.snapshot;inserted=[]
        def changing(path,**kwargs):
            result=snapshot(path,**kwargs)
            if Path(path)==out/'donor.json' and not inserted:
                (out/'unexpected-during-validation').write_bytes(b'fixture');inserted.append(True)
            return result
        with mock.patch.object(verifier,'snapshot',side_effect=changing):
            with self.assertRaises(ValueError):verifier.validate_refresh(out)
        self.assertTrue(inserted)

    def test_extra_shard_created_during_acquisition_is_rejected(self):
        checkpoint,donor,_=fixture(self.base);snapshot=verifier.snapshot;inserted=[]
        def changing(path,**kwargs):
            result=snapshot(path,**kwargs)
            if Path(path).suffix=='.safetensors' and not inserted:
                (checkpoint/'unexpected.safetensors').write_bytes(b'fixture');inserted.append(True)
            return result
        with mock.patch.object(verifier,'snapshot',side_effect=changing):
            with self.assertRaises(ValueError):verifier.verify(checkpoint,donor,self.base/'refresh',test_only=True)
        self.assertTrue(inserted)

    def test_frozen_receipt_link_is_refused_before_opening_its_target(self):
        _,_,_,out,_=self.run_fixture();receipt=out/'receipt.json';target=self.base/'outside-bundle-receipt'
        receipt.rename(target);receipt.symlink_to(target);opens=[];original=os.open
        def observed(path,*args,**kwargs):
            if str(path)==str(target):opens.append(str(path))
            return original(path,*args,**kwargs)
        with mock.patch.object(verifier.os,'open',side_effect=observed):
            with self.assertRaises(ValueError):verifier.validate_refresh(out)
        self.assertEqual(opens,[])


if __name__=='__main__':unittest.main()
