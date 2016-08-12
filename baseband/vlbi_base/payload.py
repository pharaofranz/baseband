"""
Base definitions for VLBI payloads, used for VDIF and Mark 5B.

Defines a payload class VLBIPayloadBase that can be used to hold the words
corresponding to a frame payload, providing access to the values encoded in
it as a numpy array.
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import operator
from functools import reduce
import numpy as np


__all__ = ['VLBIPayloadBase', 'DTYPE_WORD']


DTYPE_WORD = np.dtype('<u4')
"""Dtype for 32-bit unsigned integers, with least signicant byte first."""


class VLBIPayloadBase(object):
    """Container for decoding and encoding VLBI payloads.

    Any subclass should define dictionaries ``_decoders`` and ``_encoders``,
    which hold functions that decode/encode the payload words to/from ndarray.
    These dictionaries are assumed to be indexed by ``(bps, complex_data)``.

    Parameters
    ----------
    words : ndarray
        Array containg LSB unsigned words (with the right size) that
        encode the payload.
    bps : int
        Number of bits per sample part (i.e., per channel and per real or
        imaginary component).  Default: 2.
    sample_shape : tuple
        Shape of the samples; e.g., (nchan,).  Default: ().
    complex_data : bool
        Whether data is complex or float.  Default: False.
    """
    # Possible fixed payload size.
    _size = None
    # To be defined by subclasses.
    _encoders = {}
    _decoders = {}

    def __init__(self, words, bps=2, sample_shape=(), complex_data=False):
        self.words = words
        self.sample_shape = sample_shape
        self.bps = bps
        self.complex_data = complex_data
        self._bpfs = (self.bps * (2 if self.complex_data else 1) *
                      reduce(operator.mul, self.sample_shape, 1))
        if self._size is not None and self._size != self.size:
            raise ValueError("Encoded data should have length {0}"
                             .format(self._size))

    @classmethod
    def fromfile(cls, fh, *args, **kwargs):
        """Read payload from file handle and decode it into data.

        Parameters
        ----------
        fh : filehandle
            Handle to the file from which data is read
        payloadsize : int
            Number of bytes to read (default: as given in ``cls._size``.

        Any other (keyword) arguments are passed on to the class initialiser.
        """
        payloadsize = kwargs.pop('payloadsize', cls._size)
        if payloadsize is None:
            raise ValueError("Payloadsize should be given as an argument "
                             "if no default is defined on the class.")
        s = fh.read(payloadsize)
        if len(s) < payloadsize:
            raise EOFError("Could not read full payload.")
        return cls(np.fromstring(s, dtype=DTYPE_WORD), *args, **kwargs)

    def tofile(self, fh):
        """Write VLBI payload to filehandle."""
        return fh.write(self.words.tostring())

    @classmethod
    def fromdata(cls, data, bps=2):
        """Encode data as a VLBI payload.

        Parameters
        ----------
        data : ndarray
            Data to be encoded. The last dimension is taken as the number of
            channels.
        bps : int
            Number of bits per sample to use (for complex data, for real and
            imaginary part separately; default: 2).
        """
        complex_data = data.dtype.kind == 'c'
        try:
            encoder = cls._encoders[bps, complex_data]
        except KeyError:
            raise ValueError("{0} cannot encode {1} data with {2} bits"
                             .format(cls.__name__, 'complex' if complex_data
                                     else 'real', bps))
        words = encoder(data.ravel()).view(DTYPE_WORD)
        return cls(words, sample_shape=data.shape[1:], bps=bps,
                   complex_data=complex_data)

    def todata(self, data=None):
        """Decode the payload.

        Parameters
        ----------
        data : ndarray or None
            If given, used to decode the payload into.  It should have the
            right size to store it.  Its shape is not changed.
        """
        decoder = self._decoders[self.bps, self.complex_data]
        out = decoder(self.words, out=data)
        return out.reshape(self.shape) if data is None else data

    def __array__(self, dtype=None):
        """Interface to arrays."""
        if dtype is None or dtype == self.dtype:
            return self.data
        else:
            return self.data.astype(dtype)

    @property
    def size(self):
        """Size in bytes of payload."""
        return len(self.words) * self.words.dtype.itemsize

    @property
    def nsample(self):
        """Number of samples in the payload."""
        return self.size * 8 // self._bpfs

    @property
    def shape(self):
        """Shape of the decoded data array (nsample, sample_shape)."""
        return (self.nsample,) + self.sample_shape

    @property
    def dtype(self):
        """Type of the decoded data array."""
        return np.dtype(np.complex64 if self.complex_data else np.float32)

    def _item_to_slices(self, item):
        """Get word and data slices required to get given item.

        Returns ``words_slice`` and ``data_slice`` such that if one decodes
        ``self.words[words_slice]`` the returned data is the smallest possible
        array that includes the requested item or slice (as ``data_slice``).
        """
        is_slice = isinstance(item, slice)
        if is_slice:
            start, stop, step = item.indices(self.nsample)
            n = stop - start
            if step == 1:
                step = None
        else:
            try:
                item = item.__index__()
            except:
                raise TypeError("{0} object can only be indexed or sliced."
                                .format(type(self)))
            if item < 0:
                item += self.nsample

            if not (0 <= item < self.nsample):
                raise IndexError("{0} index out of range.".format(type(self)))

            start, stop, step, n = item, item+1, 1, 1

        if n == self.nsample:
            words_slice = slice(None)
            data_slice = slice(None, None, step) if is_slice else 0

        else:
            bpw = 8 * self.words.dtype.itemsize
            bpfs = self._bpfs
            if bpfs % bpw == 0:
                # Each full sample requires one or more encoded words.
                # Get corresponding range in words required, and decode those.
                wpfs = bpfs // bpw
                words_slice = slice(start * wpfs, stop * wpfs)
                data_slice = slice(None, None, step) if is_slice else 0

            elif bpw % bpfs == 0:
                # Each word contains multiple samples.
                # Get words in which required samples are contained.
                fspw = bpw // bpfs
                w_start, o_start = divmod(start, fspw)
                w_stop, o_stop = divmod(stop, fspw)

                words_slice = slice(w_start, w_stop + 1 if o_stop else w_stop)
                data_slice = slice(o_start if o_start else None,
                                   o_start + n if o_stop else None,
                                   step) if is_slice else o_start

            else:
                raise TypeError("Do not know how to extract data when full "
                                "samples have {0} bits and words have {1} bits"
                                .format(bpfs, bpw))

        return words_slice, data_slice

    def __getitem__(self, item=()):
        decoder = self._decoders[self.bps, False]
        if item is () or item == slice(None):
            data = decoder(self.words)
            if self.complex_data:
                data = data.view(self.dtype)
            return data.reshape(self.shape)

        words_slice, data_slice = self._item_to_slices(item)
        return (decoder(self.words[words_slice]).view(self.dtype)
                .reshape(-1, *self.sample_shape)[data_slice])

    def __setitem__(self, item, data):
        if item is () or item == slice(None):
            words_slice = data_slice = slice(None)
        else:
            words_slice, data_slice = self._item_to_slices(item)

        data = np.asanyarray(data)
        # Avoid decoding if possible.
        if not (data_slice == slice(None) and
                data.shape[-len(self.sample_shape):] == self.sample_shape and
                data.dtype.kind == self.dtype.kind):
            decoder = self._decoders[self.bps, False]
            current_data = decoder(self.words[words_slice])
            if self.complex_data:
                current_data = current_data.view(self.dtype)
            current_data.shape = (-1,) + self.sample_shape
            current_data[data_slice] = data
            data = current_data

        data = data.ravel()
        if data.dtype.kind == 'c':
            data = data.view(data.real.dtype)

        encoder = self._encoders[self.bps, False]
        self.words[words_slice] = encoder(data)

    data = property(__getitem__, doc="Full decoded payload.")

    def __eq__(self, other):
        return (type(self) is type(other) and
                self.shape == other.shape and
                self.dtype == other.dtype and
                (self.words is other.words or
                 np.all(self.words == other.words)))

    def __ne__(self, other):
        return not self.__eq__(other)
