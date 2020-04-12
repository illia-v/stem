"""
Unit tests for stem.descriptor.remote.
"""

import http.client
import socket
import time
import unittest

import stem
import stem.descriptor
import stem.descriptor.remote
import stem.util.str_tools
import test.require

from unittest.mock import patch, Mock, MagicMock

from stem.descriptor.remote import Compression
from test.unit.descriptor import read_resource

TEST_RESOURCE = '/tor/server/fp/9695DFC35FFEB861329B9F1AB04C46397020CE31'

# Output from requesting moria1's descriptor from itself...
# % curl http://128.31.0.39:9131/tor/server/fp/9695DFC35FFEB861329B9F1AB04C46397020CE31

TEST_DESCRIPTOR = b"""\
router moria1 128.31.0.34 9101 0 9131
platform Tor 0.2.5.0-alpha-dev on Linux
protocols Link 1 2 Circuit 1
published 2013-07-05 23:48:52
fingerprint 9695 DFC3 5FFE B861 329B 9F1A B04C 4639 7020 CE31
uptime 1818933
bandwidth 512000 62914560 1307929
extra-info-digest 17D0142F6EBCDF60160EB1794FA6C9717D581F8C
caches-extra-info
onion-key
-----BEGIN RSA PUBLIC KEY-----
MIGJAoGBALzd4bhz1usB7wpoaAvP+BBOnNIk7mByAKV6zvyQ0p1M09oEmxPMc3qD
AAm276oJNf0eq6KWC6YprzPWFsXEIdXSqA6RWXCII1JG/jOoy6nt478BkB8TS9I9
1MJW27ppRaqnLiTmBmM+qzrsgJGwf+onAgUKKH2GxlVgahqz8x6xAgMBAAE=
-----END RSA PUBLIC KEY-----
signing-key
-----BEGIN RSA PUBLIC KEY-----
MIGJAoGBALtJ9uD7cD7iHjqNA3AgsX9prES5QN+yFQyr2uOkxzhvunnaf6SNhzWW
bkfylnMrRm/qCz/czcjZO6N6EKHcXmypehvP566B7gAQ9vDsb+l7VZVWgXvzNc2s
tl3P7qpC08rgyJh1GqmtQTCesIDqkEyWxwToympCt09ZQRq+fIttAgMBAAE=
-----END RSA PUBLIC KEY-----
hidden-service-dir
contact 1024D/28988BF5 arma mit edu
ntor-onion-key 9ZVjNkf/iLEnD685SpC5kcDytQ7u5ViiI9JOftdbE0k=
reject *:*
router-signature
-----BEGIN SIGNATURE-----
Y8Tj2e7mPbFJbguulkPEBVYzyO57p4btpWEXvRMD6vxIh/eyn25pehg5dUVBtZlL
iO3EUE0AEYah2W9gdz8t+i3Dtr0zgqLS841GC/TyDKCm+MKmN8d098qnwK0NGF9q
01NZPuSqXM1b6hnl2espFzL7XL8XEGRU+aeg+f/ukw4=
-----END SIGNATURE-----
"""

HEADER = '\r\n'.join([
  'Date: Fri, 13 Apr 2018 16:35:50 GMT',
  'Content-Type: application/octet-stream',
  'X-Your-Address-Is: 97.103.17.56',
  'Pragma: no-cache',
  'Content-Encoding: %s',
])


def _orport_mock(data, encoding = 'identity', response_code_header = None):
  if response_code_header is None:
    response_code_header = b'HTTP/1.0 200 OK\r\n'

  data = response_code_header + stem.util.str_tools._to_bytes(HEADER % encoding) + b'\r\n\r\n' + data
  cells = []

  for hunk in [data[i:i + 50] for i in range(0, len(data), 50)]:
    cell = Mock()
    cell.data = hunk
    cells.append(cell)

  connect_mock = MagicMock()
  relay_mock = connect_mock().__enter__()
  circ_mock = relay_mock.create_circuit().__enter__()
  circ_mock.directory.return_value = data
  return connect_mock


def _dirport_mock(data, encoding = 'identity'):
  dirport_mock = Mock()
  dirport_mock().read.return_value = data

  headers = http.client.HTTPMessage()

  for line in HEADER.splitlines():
    key, value = line.split(': ', 1)
    headers.add_header(key, encoding if key == 'Content-Encoding' else value)

  dirport_mock().headers = headers

  return dirport_mock


class TestDescriptorDownloader(unittest.TestCase):
  def tearDown(self):
    # prevent our mocks from impacting other tests
    stem.descriptor.remote.SINGLETON_DOWNLOADER = None

  @patch('stem.client.Relay.connect', _orport_mock(TEST_DESCRIPTOR))
  def test_using_orport(self):
    """
    Download a descriptor through the ORPort.
    """

    reply = stem.descriptor.remote.their_server_descriptor(
      endpoints = [stem.ORPort('12.34.56.78', 1100)],
      validate = True,
      skip_crypto_validation = not test.require.CRYPTOGRAPHY_AVAILABLE,
    )

    self.assertEqual(1, len(list(reply)))
    self.assertEqual('moria1', list(reply)[0].nickname)
    self.assertEqual(5, len(reply.reply_headers))

  def test_orport_response_code_headers(self):
    """
    When successful Tor provides a '200 OK' status, but we should accept other 2xx
    response codes, reason text, and recognize HTTP errors.
    """

    response_code_headers = (
      b'HTTP/1.0 200 OK\r\n',
      b'HTTP/1.0 205 OK\r\n',
      b'HTTP/1.0 200 This is also alright\r\n',
    )

    for header in response_code_headers:
      with patch('stem.client.Relay.connect', _orport_mock(TEST_DESCRIPTOR, response_code_header = header)):
        stem.descriptor.remote.their_server_descriptor(
          endpoints = [stem.ORPort('12.34.56.78', 1100)],
          validate = True,
          skip_crypto_validation = not test.require.CRYPTOGRAPHY_AVAILABLE,
        ).run()

    with patch('stem.client.Relay.connect', _orport_mock(TEST_DESCRIPTOR, response_code_header = b'HTTP/1.0 500 Kaboom\r\n')):
      request = stem.descriptor.remote.their_server_descriptor(
        endpoints = [stem.ORPort('12.34.56.78', 1100)],
        validate = True,
        skip_crypto_validation = not test.require.CRYPTOGRAPHY_AVAILABLE,
      )

      self.assertRaisesRegexp(stem.ProtocolError, "^Response should begin with HTTP success, but was 'HTTP/1.0 500 Kaboom'", request.run)

  @patch('urllib.request.urlopen', _dirport_mock(TEST_DESCRIPTOR))
  def test_using_dirport(self):
    """
    Download a descriptor through the DirPort.
    """

    reply = stem.descriptor.remote.their_server_descriptor(
      endpoints = [stem.DirPort('12.34.56.78', 1100)],
      validate = True,
      skip_crypto_validation = not test.require.CRYPTOGRAPHY_AVAILABLE,
    )

    self.assertEqual(1, len(list(reply)))
    self.assertEqual('moria1', list(reply)[0].nickname)
    self.assertEqual(5, len(reply.reply_headers))

  def test_gzip_url_override(self):
    query = stem.descriptor.remote.Query(TEST_RESOURCE + '.z', compression = Compression.PLAINTEXT, start = False)
    self.assertEqual([stem.descriptor.Compression.GZIP], query.compression)
    self.assertEqual(TEST_RESOURCE, query.resource)

  @patch('urllib.request.urlopen', _dirport_mock(read_resource('compressed_identity'), encoding = 'identity'))
  def test_compression_plaintext(self):
    """
    Download a plaintext descriptor.
    """

    descriptors = list(stem.descriptor.remote.get_server_descriptors(
      '9695DFC35FFEB861329B9F1AB04C46397020CE31',
      compression = Compression.PLAINTEXT,
      validate = True,
      skip_crypto_validation = not test.require.CRYPTOGRAPHY_AVAILABLE,
    ))

    self.assertEqual(1, len(descriptors))
    self.assertEqual('moria1', descriptors[0].nickname)

  @patch('urllib.request.urlopen', _dirport_mock(read_resource('compressed_gzip'), encoding = 'gzip'))
  def test_compression_gzip(self):
    """
    Download a gip compressed descriptor.
    """

    descriptors = list(stem.descriptor.remote.get_server_descriptors(
      '9695DFC35FFEB861329B9F1AB04C46397020CE31',
      compression = Compression.GZIP,
      validate = True,
      skip_crypto_validation = not test.require.CRYPTOGRAPHY_AVAILABLE,
    ))

    self.assertEqual(1, len(descriptors))
    self.assertEqual('moria1', descriptors[0].nickname)

  @patch('urllib.request.urlopen', _dirport_mock(read_resource('compressed_zstd'), encoding = 'x-zstd'))
  def test_compression_zstd(self):
    """
    Download a zstd compressed descriptor.
    """

    if not Compression.ZSTD.available:
      self.skipTest('(requires zstd module)')

    descriptors = list(stem.descriptor.remote.get_server_descriptors(
      '9695DFC35FFEB861329B9F1AB04C46397020CE31',
      compression = Compression.ZSTD,
      validate = True,
    ))

    self.assertEqual(1, len(descriptors))
    self.assertEqual('moria1', descriptors[0].nickname)

  @patch('urllib.request.urlopen', _dirport_mock(read_resource('compressed_lzma'), encoding = 'x-tor-lzma'))
  def test_compression_lzma(self):
    """
    Download a lzma compressed descriptor.
    """

    if not Compression.LZMA.available:
      self.skipTest('(requires lzma module)')

    descriptors = list(stem.descriptor.remote.get_server_descriptors(
      '9695DFC35FFEB861329B9F1AB04C46397020CE31',
      compression = Compression.LZMA,
      validate = True,
    ))

    self.assertEqual(1, len(descriptors))
    self.assertEqual('moria1', descriptors[0].nickname)

  @patch('urllib.request.urlopen')
  def test_each_getter(self, dirport_mock):
    """
    Surface level exercising of each getter method for downloading descriptors.
    """

    downloader = stem.descriptor.remote.get_instance()

    downloader.get_server_descriptors()
    downloader.get_extrainfo_descriptors()
    downloader.get_microdescriptors('test-hash')
    downloader.get_consensus()
    downloader.get_vote(stem.directory.Authority.from_cache()['moria1'])
    downloader.get_key_certificates()
    downloader.get_bandwidth_file()
    downloader.get_detached_signatures()

  @patch('urllib.request.urlopen', _dirport_mock(TEST_DESCRIPTOR))
  def test_reply_headers(self):
    query = stem.descriptor.remote.get_server_descriptors('9695DFC35FFEB861329B9F1AB04C46397020CE31', start = False)
    self.assertEqual(None, query.reply_headers)  # initially we don't have a reply
    query.run()

    self.assertEqual('Fri, 13 Apr 2018 16:35:50 GMT', query.reply_headers.get('date'))
    self.assertEqual('application/octet-stream', query.reply_headers.get('content-type'))
    self.assertEqual('97.103.17.56', query.reply_headers.get('x-your-address-is'))
    self.assertEqual('no-cache', query.reply_headers.get('pragma'))
    self.assertEqual('identity', query.reply_headers.get('content-encoding'))

    # getting headers should be case insensitive
    self.assertEqual('identity', query.reply_headers.get('CoNtEnT-ENCODING'))

    # request a header that isn't present
    self.assertEqual(None, query.reply_headers.get('no-such-header'))
    self.assertEqual('default', query.reply_headers.get('no-such-header', 'default'))

    descriptors = list(query)
    self.assertEqual(1, len(descriptors))
    self.assertEqual('moria1', descriptors[0].nickname)

  @patch('urllib.request.urlopen', _dirport_mock(TEST_DESCRIPTOR))
  def test_query_download(self):
    """
    Check Query functionality when we successfully download a descriptor.
    """

    query = stem.descriptor.remote.Query(
      TEST_RESOURCE,
      'server-descriptor 1.0',
      endpoints = [stem.DirPort('128.31.0.39', 9131)],
      compression = Compression.PLAINTEXT,
      validate = True,
      skip_crypto_validation = not test.require.CRYPTOGRAPHY_AVAILABLE,
    )

    self.assertEqual(stem.DirPort('128.31.0.39', 9131), query._pick_endpoint())

    descriptors = list(query)
    self.assertEqual(1, len(descriptors))
    desc = descriptors[0]

    self.assertEqual('moria1', desc.nickname)
    self.assertEqual('128.31.0.34', desc.address)
    self.assertEqual('9695DFC35FFEB861329B9F1AB04C46397020CE31', desc.fingerprint)
    self.assertEqual(TEST_DESCRIPTOR, desc.get_bytes())

  @patch('urllib.request.urlopen', _dirport_mock(b'some malformed stuff'))
  def test_query_with_malformed_content(self):
    """
    Query with malformed descriptor content.
    """

    query = stem.descriptor.remote.Query(
      TEST_RESOURCE,
      'server-descriptor 1.0',
      endpoints = [stem.DirPort('128.31.0.39', 9131)],
      compression = Compression.PLAINTEXT,
      validate = True,
    )

    # checking via the iterator

    descriptors = list(query)
    self.assertEqual(0, len(descriptors))
    self.assertEqual(ValueError, type(query.error))
    self.assertEqual("Descriptor must have a 'router' entry", str(query.error))

    # check via the run() method

    self.assertRaises(ValueError, query.run)

  @patch('urllib.request.urlopen')
  def test_query_with_timeout(self, dirport_mock):
    def urlopen_call(*args, **kwargs):
      time.sleep(0.06)
      raise socket.timeout('connection timed out')

    dirport_mock.side_effect = urlopen_call

    query = stem.descriptor.remote.Query(
      TEST_RESOURCE,
      'server-descriptor 1.0',
      endpoints = [stem.DirPort('128.31.0.39', 9131)],
      fall_back_to_authority = False,
      timeout = 0.1,
      validate = True,
    )

    # After two requests we'll have reached our total permissable timeout.
    # It would be nice to check that we don't make a third, but this
    # assertion has proved unreliable so only checking for the exception.

    self.assertRaises(stem.DownloadTimeout, query.run)

  def test_query_with_invalid_endpoints(self):
    invalid_endpoints = {
      'hello': "'h' is a str.",
      ('hello',): "'hello' is a str.",
      (('hello',),): "'('hello',)' is a tuple.",
      (15,): "'15' is a int.",
    }

    for endpoints, error_suffix in invalid_endpoints.items():
      expected_error = 'Endpoints must be an stem.ORPort or stem.DirPort. ' + error_suffix
      self.assertRaisesWith(ValueError, expected_error, stem.descriptor.remote.Query, TEST_RESOURCE, 'server-descriptor 1.0', endpoints = endpoints)

  @patch('urllib.request.urlopen', _dirport_mock(TEST_DESCRIPTOR))
  def test_can_iterate_multiple_times(self):
    query = stem.descriptor.remote.Query(
      TEST_RESOURCE,
      'server-descriptor 1.0',
      endpoints = [stem.DirPort('128.31.0.39', 9131)],
      compression = Compression.PLAINTEXT,
      validate = True,
      skip_crypto_validation = not test.require.CRYPTOGRAPHY_AVAILABLE,
    )

    # check that iterating over the query provides the descriptors each time

    self.assertEqual(1, len(list(query)))
    self.assertEqual(1, len(list(query)))
    self.assertEqual(1, len(list(query)))
