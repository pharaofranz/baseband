from ..utils import bcd_encode, bcd_decode, CRC
from ..header import HeaderParser, VLBIHeaderBase, four_word_struct
from ..payload import VLBIPayloadBase
from ..frame import VLBIFrameBase


from copy import copy
import io
import numpy as np
from astropy.tests.helper import pytest


def encode_1bit(values):
    return np.packbits(values.ravel())


def decode_1bit(values):
    return np.unpackbits(values.view(np.uint8)).astype(np.float32)


def encode_8bit(values):
    return np.clip(np.round(values),
                   -128, 127).astype(np.int8)


def decode_8bit(values):
    return values.view(np.int8).astype(np.float32)


class Payload(VLBIPayloadBase):
    _encoders = {1: encode_1bit,
                 8: encode_8bit}
    _decoders = {1: decode_1bit,
                 8: decode_8bit}


class TestBCD(object):
    def test_bcd_decode(self):
        assert bcd_decode(0x1) == 1
        assert bcd_decode(0x9123) == 9123
        with pytest.raises(ValueError):
            bcd_decode(0xf)
        decoded = bcd_decode(np.array([0x1, 0x9123]))
        assert isinstance(decoded, np.ndarray)
        assert np.all(decoded == np.array([1, 9123]))
        with pytest.raises(ValueError):
            bcd_decode(np.array([0xf, 9123]))
        with pytest.raises(TypeError):
            bcd_decode([1, 2])

    def test_bcd_encode(self):
        assert bcd_encode(1) == 0x1
        assert bcd_encode(9123) == 0x9123
        with pytest.raises(ValueError):
            bcd_encode('bla')

    def test_roundtrip(self):
        assert bcd_decode(bcd_encode(15)) == 15
        assert bcd_decode(bcd_encode(8765)) == 8765
        a = np.array([1, 9123])
        assert np.all(bcd_decode(bcd_encode(a)) == a)


class TestVLBIBase(object):
    def setup(self):
        self.header_parser = HeaderParser(
            (('x0_16_4', (0, 16, 4)),
             ('x0_31_1', (0, 31, 1, False)),
             ('x1_0_32', (1, 0, 32)),
             ('x2_0_64', (2, 0, 64, 1 << 32))))

        class Header(VLBIHeaderBase):
            _struct = four_word_struct
            _header_parser = self.header_parser
            payloadsize = 8

        self.Header = Header
        self.header = self.Header([0x12345678, 0xffff0000, 0x0, 0xffffffff])
        self.Payload = Payload
        self.payload = Payload(np.array([0x12345678, 0xffff0000],
                                        dtype=Payload._dtype_word),
                               bps=8, sample_shape=(2,), complex_data=False)
        self.payload1bit = Payload(np.array([0x12345678]*5,
                                            dtype=Payload._dtype_word),
                                   bps=1, sample_shape=(5,), complex_data=True)

        class Frame(VLBIFrameBase):
            _header_class = Header
            _payload_class = Payload

        self.Frame = Frame
        self.frame = Frame(self.header, self.payload)

    def test_header_parser_update(self):
        extra = HeaderParser((('x4_0_32', (4, 0, 32)),))
        new = self.header_parser + extra
        assert len(new.keys()) == 5
        new = self.header_parser.copy()
        new.update(extra)
        assert len(new.keys()) == 5
        with pytest.raises(TypeError):
            self.header_parser + {'x4_0_32': (4, 0, 32)}
        with pytest.raises(TypeError):
            self.header_parser.copy().update(('x4_0_32', (4, 0, 32)))

    def test_header_basics(self):
        header = self.Header(None)
        assert header.words == [0,] * 4
        with pytest.raises(Exception):
            self.Header([1,]*5)
        with pytest.raises(Exception):
            self.Header([1,]*3)
        header = self.header.copy()
        assert header == self.header
        assert header is not self.header
        header = copy(self.header)
        assert header == self.header
        assert header is not self.header
        assert type(header.words) is list
        header.mutable = False
        assert type(header.words) is tuple
        header = self.Header(0, verify=False)
        with pytest.raises(Exception):
            header.verify()
        with pytest.raises(TypeError):
            header.mutable = True

    def test_header_fromfile(self):
        with io.BytesIO() as s:
            s.write(four_word_struct.pack(*self.header.words))
            s.seek(2)
            with pytest.raises(EOFError):
                self.Header.fromfile(s)
            s.seek(0)
            header = self.Header.fromfile(s)
        assert header == self.header

    def test_parser(self):
        """Test that parsers work as expected."""
        assert self.header['x0_16_4'] == 4
        assert self.header['x0_31_1'] is False
        assert self.header['x1_0_32'] == self.header.words[1]
        assert (self.header['x2_0_64'] ==
                self.header.words[2] + self.header.words[3] * (1 << 32))
        assert 'x0_31_1' in self.header
        assert 'bla' not in self.header
        with pytest.raises(KeyError):
            self.header['bla']
        with pytest.raises(KeyError):
            self.header['bla'] = 1
        assert self.header.x0_16_4 == 4
        with pytest.raises(AttributeError):
            self.header.xbla

    def test_make_setter(self):
        header = self.header.copy()
        header['x0_16_4'] = 0xf
        assert header.words[0] == 0x123f5678
        with pytest.raises(ValueError):
            header['x0_16_4'] = 0x10
        header['x0_31_1'] = True
        assert header.words[0] == 0x923f5678
        header['x1_0_32'] = 0x1234
        assert header.words[:2] == [0x923f5678, 0x1234]
        header['x2_0_64'] = 1
        assert header.words[2:] == [1, 0]
        header['x2_0_64'] = None
        assert header.words[2:] == [0, 1]

    def test_header_parser_class(self):
        header_parser = self.header_parser
        words = self.header.words
        header_parser['0_2_8'] = (0, 2, 8, 5)
        assert '0_2_8' in header_parser
        assert header_parser.defaults['0_2_8'] == 5
        assert header_parser.parsers['0_2_8'](words) == (words[0] >> 2) & 0xff
        small_parser = HeaderParser((('0_2_8', (0, 2, 8, 4)),))
        header_parser2 = self.header_parser + small_parser
        assert header_parser2.parsers['0_2_8'](words) == (words[0] >> 2) & 0xff
        assert header_parser2.defaults['0_2_8'] == 4
        with pytest.raises(TypeError):
            header_parser + {'0_2_8': (0, 2, 8, 4)}
        with pytest.raises(TypeError):
            header_parser + {'0_2_8': (0, 2, 8, 4)}
        with pytest.raises(Exception):
            self.HeaderParser((('0_2_32', (0, 2, 32, 4)),))
        with pytest.raises(Exception):
            self.HeaderParser((('0_2_64', (0, 2, 64, 4)),))

    def test_payload_basics(self):
        assert self.payload.complex_data is False
        assert self.payload.sample_shape == (2,)
        assert self.payload.bps == 8
        assert self.payload.shape == (4, 2)
        assert self.payload.size == 8
        assert np.all(self.payload.data.ravel() ==
                      self.payload.words.view(np.int8))
        assert np.all(np.array(self.payload).ravel() ==
                      self.payload.words.view(np.int8))
        assert np.all(np.array(self.payload, dtype=np.int8).ravel() ==
                      self.payload.words.view(np.int8))
        payload = self.Payload(self.payload.words, bps=4)
        with pytest.raises(KeyError):
            payload.data
        payload = self.Payload(self.payload.words, bps=8, complex_data=True)
        assert np.all(payload.data ==
                      self.payload.data[:, 0] + 1j * self.payload.data[:, 1])

        assert self.payload1bit.complex_data is True
        assert self.payload1bit.sample_shape == (5,)
        assert self.payload1bit.bps == 1
        assert self.payload1bit.shape == (16, 5)
        assert self.payload1bit.size == 20
        assert np.all(self.payload1bit.data.ravel() ==
                      np.unpackbits(self.payload1bit.words.view(np.uint8))
                      .astype(np.float32).view(np.complex64))

    @pytest.mark.parametrize('item', (2, slice(1, 3), (), slice(2, None),
                                      (2, 1), (slice(None), 0),
                                      (slice(1, 3), 1)))
    def test_payload_getitem_setitem(self, item):
        data = self.payload.data
        sel_data = data[item]
        assert np.all(self.payload[item] == sel_data)
        payload = self.Payload(self.payload.words.copy(), bps=8,
                               sample_shape=(2,), complex_data=False)
        assert payload == self.payload
        payload[item] = 1-sel_data
        check = self.payload.data
        check[item] = 1-sel_data
        assert np.all(payload[item] == 1-sel_data)
        assert np.all(payload.data == check)
        assert np.all(payload[:] ==
                      payload.words.view(np.int8).reshape(-1, 2))
        assert payload != self.payload
        payload[item] = sel_data
        assert np.all(payload[item] == sel_data)
        assert payload == self.payload
        payload = self.Payload.fromdata(data + 1j * data, bps=8)
        sel_data = payload.data[item]
        assert np.all(payload[item] == sel_data)
        payload[item] = 1-sel_data
        check = payload.data
        check[item] = 1-sel_data
        assert np.all(payload.data == check)

    def test_payload_bad_fbps(self):
        with pytest.raises(TypeError):
            self.payload1bit[10:11]

    def test_payload_empty_item(self):
        p11 = self.payload[1:1]
        assert p11.size == 0
        assert p11.shape == (0,) + self.payload.sample_shape
        assert p11.dtype == self.payload.dtype
        payload = self.Payload(self.payload.words.copy(), bps=8,
                               sample_shape=(2,), complex_data=False)
        payload[1:1] = 1
        assert payload == self.payload

    @pytest.mark.parametrize('item', (20, -20, (slice(None), 5)))
    def test_payload_invalid_item(self, item):
        with pytest.raises(IndexError):
            self.payload[item]

        payload = self.Payload(self.payload.words.copy(), bps=8,
                               sample_shape=(2,), complex_data=False)
        with pytest.raises(IndexError):
            payload[item] = 1

    def test_payload_invalid_item2(self):
        with pytest.raises(TypeError):
            self.payload['l']
        payload = self.Payload(self.payload.words.copy(), bps=8,
                               sample_shape=(2,), complex_data=False)
        with pytest.raises(TypeError):
            payload['l'] = 1

    def test_payload_setitem_wrong_shape(self):
        payload = self.Payload(self.payload.words.copy(), bps=8,
                               sample_shape=(2,), complex_data=False)
        with pytest.raises(ValueError):
            payload[1] = np.ones(10)

        with pytest.raises(ValueError):
            payload[1] = np.ones((2, 2))

        with pytest.raises(ValueError):
            payload[1:3] = np.ones((2, 3))

        with pytest.raises(ValueError):
            payload[1:3, 0] = np.ones((2, 2))

        with pytest.raises(ValueError):
            payload[1:3, :1] = np.ones((1, 2))

    def test_payload_fromfile(self):
        with io.BytesIO() as s:
            self.payload.tofile(s)
            s.seek(0)
            with pytest.raises(ValueError):
                self.Payload.fromfile(s)  # no size given
            s.seek(0)
            payload = self.Payload.fromfile(
                s, payloadsize=len(self.payload.words) * 4,
                sample_shape=(2,), bps=8)
        assert payload == self.payload

    def test_payload_fromdata(self):
        data = np.random.normal(0., 64., 16).reshape(16, 1)
        payload = self.Payload.fromdata(data, bps=8)
        assert payload.complex_data is False
        assert payload.sample_shape == (1,)
        assert payload.bps == 8
        assert payload.words.dtype is self.Payload._dtype_word
        assert len(payload.words) == 4
        assert payload.nsample == len(data)
        assert payload.size == 16
        payload2 = self.Payload.fromdata(self.payload.data, self.payload.bps)
        assert payload2 == self.payload
        payload3 = self.Payload.fromdata(data.ravel(), bps=8)
        assert payload3.sample_shape == ()
        assert payload3.shape == (16,)
        assert payload3 != payload
        assert np.all(payload3.data == payload.data.ravel())
        with pytest.raises(ValueError):  # don't have relevant encoder.
            self.Payload.fromdata(data, bps=4)
        payload4 = self.Payload.fromdata(data[::2, 0] + 1j * data[1::2, 0],
                                         bps=8)
        assert payload4.complex_data is True
        assert payload4.sample_shape == ()
        assert payload4.shape == (8,)
        assert payload4 != payload
        assert np.all(payload4.words == payload.words)

    def test_frame_basics(self):
        assert self.frame.header is self.header
        assert self.frame.payload is self.payload
        assert self.frame.shape == self.payload.shape
        assert np.all(self.frame.data == self.payload.data)
        assert np.all(np.array(self.frame) == np.array(self.payload))
        assert np.all(np.array(self.frame, dtype=np.float64) ==
                      np.array(self.payload))
        assert self.frame.valid is True
        frame = self.Frame(self.header, self.payload, valid=False)
        assert np.all(frame.data == 0.)
        frame.invalid_data_value = 1.
        assert np.all(frame.data == 1.)

        assert 'x2_0_64' in self.frame
        assert self.frame['x2_0_64'] == self.header['x2_0_64']
        for item in (3, (1, 1), slice(0, 2)):
            assert np.all(self.frame[item] == self.frame.payload[item])

        frame2 = self.Frame(self.header.copy(),
                            self.Payload.fromdata(self.payload.data,
                                                  bps=self.payload.bps))

        assert frame2.header == self.header
        assert frame2.payload == self.payload
        assert frame2 == self.frame
        frame2['x2_0_64'] = 0x1
        assert frame2['x2_0_64'] == 0x1
        assert frame2.header != self.header
        frame2[3, 1] = 5.
        assert frame2[3, 1] == 5
        assert frame2.payload != self.payload

    def test_frame_fromfile(self):
        with io.BytesIO() as s:
            self.frame.tofile(s)
            s.seek(0)
            frame = self.Frame.fromfile(s, payloadsize=self.payload.size,
                                        sample_shape=(2,), bps=8)
        assert frame == self.frame

    def test_frame_fromdata(self):
        frame = self.Frame.fromdata(self.frame.data, self.header, bps=8)
        assert frame == self.frame
        frame2 = self.Frame.fromdata(self.frame.data, self.header,
                                     bps=8, valid=False)
        assert np.all(frame2.data == 0.)


def test_crc():
    # Test example from age 4 of
    # http://www.haystack.mit.edu/tech/vlbi/mark5/docs/230.3.pdf
    stream = '0000 002D 0330 0000' + 'FFFF FFFF' + '4053 2143 3805 5'
    crc_expected = '284'
    crc12 = CRC(0x180f)
    stream = stream.replace(' ', '').lower()
    istream = int(stream, base=16)
    assert '{:037x}'.format(istream) == stream
    bitstream = np.array([((istream & (1 << bit)) != 0)
                          for bit in range(37*4-1, -1, -1)], np.bool)
    crcstream = crc12(bitstream)
    crc = np.bitwise_or.reduce(crcstream.astype(np.uint32) <<
                               np.arange(11, -1, -1))
    assert '{:03x}'.format(crc) == crc_expected
    fullstream = np.hstack((bitstream, crcstream))
    assert crc12.check(fullstream)
