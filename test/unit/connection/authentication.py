"""
Unit tests for the stem.connection.authenticate function.

Under the covers the authentiate function really just translates a
PROTOCOLINFO response into authenticate_* calls, then does prioritization
on the exceptions if they all fail.

This monkey patches the various functions authenticate relies on to exercise
various error conditions, and make sure that the right exception is raised.
"""

import unittest

import stem.connection
import test

from unittest.mock import patch

from stem.response import ControlMessage
from stem.util import log
from test.unit.async_util import (
  async_test,
  coro_func_raising_exc,
  coro_func_returning_value,
)


class TestAuthenticate(unittest.TestCase):
  @patch('stem.connection.get_protocolinfo')
  @patch('stem.connection.authenticate_none')
  @async_test
  async def test_with_get_protocolinfo(self, authenticate_none_mock, get_protocolinfo_mock):
    """
    Tests the authenticate() function when it needs to make a get_protocolinfo.
    """

    # tests where get_protocolinfo succeeds

    authenticate_none_mock.side_effect = coro_func_returning_value(None)

    protocolinfo_message = ControlMessage.from_str('250-PROTOCOLINFO 1\r\n250 OK\r\n', 'PROTOCOLINFO')
    protocolinfo_message.auth_methods = (stem.connection.AuthMethod.NONE, )
    get_protocolinfo_mock.side_effect = coro_func_returning_value(protocolinfo_message)

    await stem.connection.authenticate(None)

    # tests where get_protocolinfo raises an exception

    get_protocolinfo_mock.side_effect = stem.ProtocolError
    with self.assertRaises(stem.connection.IncorrectSocketType):
      await stem.connection.authenticate(None)

    get_protocolinfo_mock.side_effect = stem.SocketError
    with self.assertRaises(stem.connection.AuthenticationFailure):
      await stem.connection.authenticate(None)

  @patch('stem.connection.authenticate_none')
  @patch('stem.connection.authenticate_password')
  @patch('stem.connection.authenticate_cookie')
  @patch('stem.connection.authenticate_safecookie')
  @async_test
  async def test_all_use_cases(self, authenticate_safecookie_mock, authenticate_cookie_mock, authenticate_password_mock, authenticate_none_mock):
    """
    Does basic validation that all valid use cases for the PROTOCOLINFO input
    and dependent functions result in either success or a AuthenticationFailed
    subclass being raised.
    """

    # mute the logger for this test since otherwise the output is overwhelming

    stem_logger = log.get_logger()
    stem_logger.setLevel(log.logging_level(None))

    # exceptions that the authentication functions are documented to raise

    all_auth_none_exc = (
      None,
      stem.connection.OpenAuthRejected(None),
      stem.ControllerError(None))

    all_auth_password_exc = (
      None,
      stem.connection.PasswordAuthRejected(None),
      stem.connection.IncorrectPassword(None),
      stem.ControllerError(None))

    all_auth_cookie_exc = (
      None,
      stem.connection.CookieAuthRejected(None, False, None),
      stem.connection.IncorrectCookieValue(None, False, None),
      stem.connection.IncorrectCookieSize(None, False, None),
      stem.connection.UnreadableCookieFile(None, False, None),
      stem.connection.AuthChallengeFailed(None, None),
      stem.ControllerError(None))

    auth_method_combinations = test.get_all_combinations([
      stem.connection.AuthMethod.NONE,
      stem.connection.AuthMethod.PASSWORD,
      stem.connection.AuthMethod.COOKIE,
      stem.connection.AuthMethod.SAFECOOKIE,
      stem.connection.AuthMethod.UNKNOWN,
    ], include_empty = True)

    protocolinfo = ControlMessage.from_str('250-PROTOCOLINFO 1\r\n250 OK\r\n', 'PROTOCOLINFO')
    protocolinfo.cookie_path = '/tmp/blah'

    for auth_methods in auth_method_combinations:
      for auth_none_exc in all_auth_none_exc:
        for auth_password_exc in all_auth_password_exc:
          for auth_cookie_exc in all_auth_cookie_exc:
            # Skip iteration if it's to test exceptions for authentication
            # we're not using.

            if auth_none_exc and stem.connection.AuthMethod.NONE not in auth_methods:
              continue
            elif auth_password_exc and stem.connection.AuthMethod.PASSWORD not in auth_methods:
              continue
            elif auth_cookie_exc and stem.connection.AuthMethod.COOKIE not in auth_methods and stem.connection.AuthMethod.SAFECOOKIE not in auth_methods:
              continue

            # Determine if the authenticate() call will succeed and mock each
            # of the authenticate_* function to raise its given exception.
            #
            # This implementation is slightly inaccurate in a couple regards...
            # a. it raises safecookie exceptions from authenticate_cookie()
            # b. exceptions raised by authenticate_cookie() and
            #    authenticate_safecookie() are always the same
            #
            # However, adding another loop for safe_cookie exceptions means
            # multiplying our runtime many fold. This exercises everything that
            # matters so the above inaccuracies seem fine.

            expect_success = False
            protocolinfo.auth_methods = auth_methods

            for auth_method in auth_methods:
              if auth_method == stem.connection.AuthMethod.NONE:
                auth_mock, raised_exc = authenticate_none_mock, auth_none_exc
              elif auth_method == stem.connection.AuthMethod.PASSWORD:
                auth_mock, raised_exc = authenticate_password_mock, auth_password_exc
              elif auth_method == stem.connection.AuthMethod.COOKIE:
                auth_mock, raised_exc = authenticate_cookie_mock, auth_cookie_exc
              elif auth_method == stem.connection.AuthMethod.SAFECOOKIE:
                auth_mock, raised_exc = authenticate_safecookie_mock, auth_cookie_exc

              if raised_exc:
                auth_mock.side_effect = coro_func_raising_exc(raised_exc)
              else:
                auth_mock.side_effect = coro_func_returning_value(None)
                expect_success = True

            if expect_success:
              await stem.connection.authenticate(None, 'blah', None, protocolinfo)
            else:
              with self.assertRaises(stem.connection.AuthenticationFailure):
                await stem.connection.authenticate(None, 'blah', None, protocolinfo)

    # revert logging back to normal
    stem_logger.setLevel(log.logging_level(log.TRACE))
