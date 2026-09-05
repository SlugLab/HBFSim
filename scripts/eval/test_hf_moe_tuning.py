"""Finite selected tuning inputs with tiny MOCK metadata and installed-source fixtures."""
from dataclasses import replace
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import hf_moe_tuning as tuning
import hf_runtime_sources as sources
from evaluation_inventory import load_hf_snapshot
from test_evaluation_inventory import hf_fixture
import test_hf_runtime_sources as source_fixtures


class TuningInputTests(unittest.TestCase):
    def setUp(self):
        fixture=source_fixtures.RuntimeSourceTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.fixture=fixture;self.site=fixture.site;self.base=fixture.base
        fixture.prepare_tuning();self.runtime=fixture.collect_tuning()
        meta=self.base/'metadata';meta.mkdir();_,bundle=hf_fixture(meta)
        self.metadata=load_hf_snapshot(bundle);self.device='TEST ONLY GPU'
        self.directory=self.site/'vllm/model_executor/layers/fused_moe/configs';self.directory.mkdir()
        self.selected=self.directory/'E=2,N=64,device_name=TEST_ONLY_GPU.json'
        self.raw=b'{"triton_version":"TEST_ONLY","1":{"BLOCK_SIZE_M":16},"32":{"BLOCK_SIZE_M":32}}\n'

    def collect(self):return tuning.collect_tuning_inputs(self.metadata,self.runtime,self.device)
    def validate(self,snapshot):return tuning.validate_tuning_inputs(snapshot,self.metadata,self.runtime,self.device)

    def test_present_input_uses_verified_w2_last_dimension_and_freezes_exact_bytes(self):
        self.selected.write_bytes(self.raw)
        with mock.patch.object(os,'scandir',side_effect=AssertionError('must not enumerate tuning files')):
            snapshot=self.collect()
        report=self.validate(snapshot)
        self.assertEqual(snapshot.config_bytes,self.raw)
        self.assertEqual(report['selected_path'],str(self.selected))
        self.assertEqual(report['weight_shapes'],{'w13':[2,128,128],'w2':[2,128,64]})
        self.assertEqual(report['selection'],'PACKAGED_JSON');self.assertTrue(report['test_only'])
        self.assertEqual(report['provenance'],'MOCK');self.assertFalse(report['device_identity_authenticated'])
        self.assertFalse(report['scientific_validation_passed'])
        tuning.recheck_tuning_inputs(snapshot,self.metadata,self.runtime,self.device)

    def test_absence_is_explicit_and_creation_after_freeze_fails_recheck(self):
        snapshot=self.collect();report=self.validate(snapshot)
        self.assertEqual(report['selection'],'INSTALLED_DEFAULTS');self.assertIsNone(snapshot.config_bytes)
        self.assertIsNone(report['file_state'])
        self.selected.write_bytes(self.raw)
        with self.assertRaises(ValueError):tuning.recheck_tuning_inputs(snapshot,self.metadata,self.runtime,self.device)

    def test_frozen_validation_never_reads_original_and_current_change_fails(self):
        self.selected.write_bytes(self.raw);snapshot=self.collect()
        self.selected.write_bytes(self.raw.replace(b'16',b'64'))
        with mock.patch.object(os,'open',side_effect=AssertionError('frozen validation opened file')):
            self.validate(snapshot)
        with self.assertRaises(ValueError):tuning.recheck_tuning_inputs(snapshot,self.metadata,self.runtime,self.device)

    def test_symlink_and_directory_alias_reject_before_opening_target(self):
        target=self.base/'foreign.json';target.write_bytes(self.raw);self.selected.symlink_to(target)
        opened=[];original=os.open
        def tracked(path,*args,**kwargs):opened.append(str(path));return original(path,*args,**kwargs)
        with mock.patch.object(os,'open',side_effect=tracked),self.assertRaises(ValueError):self.collect()
        self.assertNotIn(str(target),opened)
        self.selected.unlink();self.directory.rename(self.base/'moved-configs')
        self.directory.symlink_to(self.base/'moved-configs',target_is_directory=True)
        with self.assertRaises(ValueError):self.collect()

    def test_oversized_nonobject_and_duplicate_key_json_reject(self):
        with self.selected.open('wb') as f:f.truncate((1<<20)+1)
        with self.assertRaises(ValueError):self.collect()
        for raw in (b'[]',b'{"1":{},"1":{}}',b'{"1":NaN}'):
            self.selected.write_bytes(raw)
            with self.assertRaises(ValueError):self.collect()

    def test_directory_replacement_at_read_boundary_cannot_open_foreign_target(self):
        self.selected.write_bytes(self.raw)
        foreign=self.base/'foreign-configs';foreign.mkdir()
        target=foreign/self.selected.name;target.write_bytes(self.raw)
        original=tuning.snapshot;open_file=os.open;opened=[]
        def replaced(path,**kwargs):
            self.directory.rename(self.base/'original-configs')
            self.directory.symlink_to(foreign,target_is_directory=True)
            return original(path,**kwargs)
        def tracked(path,*args,**kwargs):opened.append(str(path));return open_file(path,*args,**kwargs)
        with mock.patch.object(tuning,'snapshot',side_effect=replaced),mock.patch.object(os,'open',side_effect=tracked):
            with self.assertRaises(ValueError):self.collect()
        self.assertNotIn(str(target),opened)

    def test_resealed_path_geometry_binding_and_directory_identity_reject(self):
        self.selected.write_bytes(self.raw);snapshot=self.collect()
        for change in ('path','geometry','runtime','metadata','directory','bool'):
            report=json.loads(snapshot.manifest_bytes)
            if change=='path':report['selected_path']=str(self.base/'other.json')
            elif change=='geometry':report['weight_shapes']['w2'][2]=128
            elif change=='runtime':report['runtime_manifest_sha256']='a'*64
            elif change=='metadata':report['metadata_receipt_sha256']='b'*64
            elif change=='directory':report['directory_chain'][0]['inode']+=1
            else:report['directory_chain'][0]['inode']=True
            altered=replace(snapshot,manifest_bytes=sources.metadata.canonical(report))
            with self.subTest(change=change),self.assertRaises(ValueError):self.validate(altered)

    def test_source_extension_required_and_device_names_cannot_select_other_paths(self):
        with self.assertRaises(ValueError):tuning.collect_tuning_inputs(self.metadata,self.fixture.collect(),self.device)
        for device in ('../GPU','GPU/other','GPU\\other','',True,'x'*257):
            with self.subTest(device=device),self.assertRaises(ValueError):tuning.collect_tuning_inputs(self.metadata,self.runtime,device)
        self.device='NVIDIA H200 SXM';report=self.validate(self.collect())
        self.assertTrue(report['selected_path'].endswith('device_name=NVIDIA_H200.json'))


if __name__=='__main__':unittest.main()
