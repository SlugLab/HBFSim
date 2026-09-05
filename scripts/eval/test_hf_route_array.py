"""TEST_ONLY frozen NPY controls; NumPy is used only to produce CPU fixtures."""
import io
import struct
import unittest
from unittest import mock

import numpy as np

from hf_route_array import decode_route_array


class RouteArrayTests(unittest.TestCase):
    def setUp(self):
        self.protocol=dict(route_shape=[39,2,2],experts=4)
        self.values=np.arange(156).reshape(39,2,2)%4

    def encoded(self,array=None):
        out=io.BytesIO();np.save(out,self.values.astype(np.int32) if array is None else array,allow_pickle=False)
        return out.getvalue()

    def custom(self,header,payload=b'',version=1):
        raw=header.encode('ascii')+b'\n'
        return b'\x93NUMPY'+bytes([version,0])+struct.pack('<H' if version==1 else '<I',len(raw))+raw+payload

    def test_numpy_integer_encodings_preserve_detached_values_without_numpy_reader(self):
        for dtype in ('int8','uint8','int16','uint16','int32','uint32','int64','uint64','>i4','>u8'):
            array=self.values.astype(dtype)
            with self.subTest(dtype=dtype),mock.patch.object(np,'load',side_effect=AssertionError('no NumPy loader')):
                result=decode_route_array(self.encoded(array),self.protocol,expected_dtype=str(array.dtype))
            self.assertEqual(result.shape,(39,2,2));self.assertEqual(result.values,tuple(self.values.reshape(-1)))
            self.assertEqual(result.dtype,str(array.dtype));self.assertIs(type(result.values),tuple)

    def test_version_two_and_exact_payload_bound(self):
        raw=self.custom("{'descr': '<i4', 'fortran_order': False, 'shape': (39, 2, 2)}",self.values.astype('<i4').tobytes(),2)
        self.assertEqual(decode_route_array(raw,self.protocol,expected_dtype='int32').shape,(39,2,2))
        for altered in (raw[:-1],raw+b'\0',b'x'*(1<<20)+b'x'):
            with self.assertRaises(ValueError):decode_route_array(altered,self.protocol,expected_dtype='int32')

    def test_malformed_or_advertised_large_header_rejects_before_body_decode(self):
        for header in (
            "{'descr': '<i4', 'fortran_order': False, 'shape': (999999999999, 2, 2)}",
            "{'descr': '<i4', 'fortran_order': False, 'shape': (True, 2, 2)}",
            "{'descr': '<i4', 'fortran_order': False, 'shape': (39, 2, 2), 'shape': (39, 2, 2)}",
            "{'descr': '<i4', 'fortran_order': 0, 'shape': (39, 2, 2)}",
            "{'descr': '<i4', 'fortran_order': False, 'shape': [39, 2, 2]}",
            "{'descr': '<i4', 'fortran_order': False, 'shape': (39, 2, 2), 'extra': None}",
            "__import__('os').system('false')",'['*60+'0'+']'*60,' '*4097):
            with self.subTest(header=header[:60]),self.assertRaises(ValueError):
                decode_route_array(self.custom(header),self.protocol,expected_dtype='int32')

    def test_noninteger_or_fortran_layout_and_wrong_dtype_reject(self):
        for array in (self.values.astype(bool),self.values.astype(float),np.asfortranarray(self.values.astype(np.int32))):
            with self.subTest(dtype=str(array.dtype)),self.assertRaises(ValueError):
                decode_route_array(self.encoded(array),self.protocol,expected_dtype=str(array.dtype))
        for descr in ('|O','<i16',"[('x', '<i4')]",'=i4'):
            with self.assertRaises(ValueError):
                decode_route_array(self.custom(repr(dict(descr=descr,fortran_order=False,shape=(39,2,2)))),self.protocol,expected_dtype='int32')
        with self.assertRaises(ValueError):decode_route_array(self.encoded(),self.protocol,expected_dtype='uint32')

    def test_route_bounds_and_topk_uniqueness_are_checked(self):
        for value in (-1,4,2):
            array=self.values.astype(np.int64);array[0,1,1]=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                decode_route_array(self.encoded(array),self.protocol,expected_dtype='int64')

    def test_invalid_protocol_or_container_and_magic_reject(self):
        for protocol in (dict(route_shape=[39,2,True],experts=4),dict(route_shape=[39,2,2],experts=True),
                         dict(route_shape=[39,2,5],experts=4),dict(route_shape=[39,2],experts=4)):
            with self.assertRaises(ValueError):decode_route_array(self.encoded(),protocol,expected_dtype='int32')
        for raw in (bytearray(self.encoded()),b'',b'not NPY',self.custom('{}',version=3)):
            with self.assertRaises(ValueError):decode_route_array(raw,self.protocol,expected_dtype='int32')


if __name__=='__main__':unittest.main()
