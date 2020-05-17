# Copyright 2011-2020, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Utility functions used by the stem library.
"""

import asyncio
import datetime
import threading

from typing import Any, Union

__all__ = [
  'conf',
  'connection',
  'enum',
  'log',
  'lru_cache',
  'ordereddict',
  'proc',
  'str_tools',
  'system',
  'term',
  'test_tools',
  'tor_tools',

  'datetime_to_unix',
]

# Beginning with Stem 1.7 we take attribute types into account when hashing
# and checking equality. That is to say, if two Stem classes' attributes are
# the same but use different types we no longer consider them to be equal.
# For example...
#
#   s1 = Schedule(classes = ['Math', 'Art', 'PE'])
#   s2 = Schedule(classes = ('Math', 'Art', 'PE'))
#
# Prior to Stem 1.7 s1 and s2 would be equal, but afterward unless Stem's
# construcotr normalizes the types they won't.
#
# This change in behavior is the right thing to do but carries some risk, so
# we provide the following constant to revert to legacy behavior. If you find
# yourself using it them please let me know (https://www.atagar.com/contact/)
# since this flag will go away in the future.

HASH_TYPES = True


def _hash_value(val: Any) -> int:
  if not HASH_TYPES:
    my_hash = 0
  else:
    # Hashing common builtins (ints, bools, etc) provide consistant values but
    # many others vary their value on interpreter invokation.

    my_hash = hash(str(type(val)))

  if isinstance(val, (tuple, list)):
    for v in val:
      my_hash = (my_hash * 1024) + hash(v)
  elif isinstance(val, dict):
    for k in sorted(val.keys()):
      my_hash = (my_hash * 2048) + (hash(k) * 1024) + hash(val[k])
  else:
    my_hash += hash(val)

  return my_hash


def datetime_to_unix(timestamp: 'datetime.datetime') -> float:
  """
  Converts a utc datetime object to a unix timestamp.

  .. versionadded:: 1.5.0

  :param datetime timestamp: timestamp to be converted

  :returns: **float** for the unix timestamp of the given datetime object
  """

  return (timestamp - datetime.datetime(1970, 1, 1)).total_seconds()


def _pubkey_bytes(key: Union['cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PrivateKey', 'cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PublicKey', 'cryptography.hazmat.primitives.asymmetric.x25519.X25519PrivateKey', 'cryptography.hazmat.primitives.asymmetric.x25519.X25519PublicKey']) -> bytes:  # type: ignore
  """
  Normalizes X25509 and ED25519 keys into their public key bytes.
  """

  if isinstance(key, bytes):
    return key
  elif isinstance(key, str):
    return key.encode('utf-8')

  try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
  except ImportError:
    raise ImportError('Key normalization requires cryptography 2.6 or later')

  if isinstance(key, (X25519PrivateKey, Ed25519PrivateKey)):
    return key.public_key().public_bytes(
      encoding = serialization.Encoding.Raw,
      format = serialization.PublicFormat.Raw,
    )
  elif isinstance(key, (X25519PublicKey, Ed25519PublicKey)):
    return key.public_bytes(
      encoding = serialization.Encoding.Raw,
      format = serialization.PublicFormat.Raw,
    )
  else:
    raise ValueError('Key must be a string or cryptographic public/private key (was %s)' % type(key).__name__)


def _hash_attr(obj: Any, *attributes: str, **kwargs: Any):
  """
  Provide a hash value for the given set of attributes.

  :param Object obj: object to be hashed
  :param list attributes: attribute names to take into account
  :param bool cache: persists hash in a '_cached_hash' object attribute
  :param class parent: include parent's hash value
  """

  is_cached = kwargs.get('cache', False)
  parent_class = kwargs.get('parent', None)
  cached_hash = getattr(obj, '_cached_hash', None)

  if is_cached and cached_hash is not None:
    return cached_hash

  my_hash = parent_class.__hash__(obj) if parent_class else 0
  my_hash = my_hash * 1024 + hash(str(type(obj)))

  for attr in attributes:
    val = getattr(obj, attr)
    my_hash = my_hash * 1024 + _hash_value(val)

  if is_cached:
    setattr(obj, '_cached_hash', my_hash)

  return my_hash


class CombinedReentrantAndAsyncioLock:
  """
  Lock that combines thread-safe reentrant and not thread-safe asyncio locks.
  """

  __slots__ = ('_r_lock', '_async_lock')

  def __init__(self):
    self._r_lock = threading.RLock()
    self._async_lock = asyncio.Lock()

  async def acquire(self):
    await self._async_lock.acquire()
    self._r_lock.acquire()

  def release(self):
    self._r_lock.release()
    self._async_lock.release()

  async def __aenter__(self):
    await self.acquire()
    return self

  async def __aexit__(self, exc_type, exc_val, exc_tb):
    self.release()


class ThreadForWrappedAsyncClass(threading.Thread):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, *kwargs)
    self.loop = asyncio.new_event_loop()
    self.setDaemon(True)

  def run(self):
    self.loop.run_forever()

  def join(self, timeout=None):
    self.loop.call_soon_threadsafe(self.loop.stop)
    super().join(timeout)
    self.loop.close()


class AsyncClassWrapper:
  _thread_for_wrapped_class: ThreadForWrappedAsyncClass
  _wrapped_instance: type

  def _init_async_class(self, async_class, *args, **kwargs):
    thread = self._thread_for_wrapped_class
    # The asynchronous class should be initialized in the thread where
    # its methods will be executed.
    if thread != threading.current_thread():
      async def init():
        return async_class(*args, **kwargs)

      return asyncio.run_coroutine_threadsafe(init(), thread.loop).result()

    return async_class(*args, **kwargs)

  def _call_async_method_soon(self, method_name, *args, **kwargs):
    return asyncio.run_coroutine_threadsafe(
      getattr(self._wrapped_instance, method_name)(*args, **kwargs),
      self._thread_for_wrapped_class.loop,
    )

  def _execute_async_method(self, method_name, *args, **kwargs):
    return self._call_async_method_soon(method_name, *args, **kwargs).result()

  def _execute_async_generator_method(self, method_name, *args, **kwargs):
    async def convert_async_generator(generator):
      return iter([d async for d in generator])

    return asyncio.run_coroutine_threadsafe(
      convert_async_generator(
        getattr(self._wrapped_instance, method_name)(*args, **kwargs),
      ),
      self._thread_for_wrapped_class.loop,
    ).result()
