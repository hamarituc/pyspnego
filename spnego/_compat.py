# Copyright: (c) 2020, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type  # noqa (fixes E402 for the imports below)

import sys

# TODO: Remove once Python 2.7 is dropped, use only int.
if sys.version_info[0] == 2:
    integer_types = (int, long)
else:
    integer_types = int


# TODO: Remove once Python 2.7 is dropped, use typing directly.
try:
    from typing import (
        Callable,
        Dict,
        List,
        Optional,
        Tuple,
        Union,
    )

except ImportError:
    Callable = any
    Dict = any
    List = any
    Optional = any
    Tuple = any
    Union = any


# TODO: Remove once Python 2.7 is dropped, use 'class Name(metadata=metaclass):' instead.
def add_metaclass(metaclass):
    """Class decorator for creating a class with a metaclass. This has been copied from six under the MIT license. """
    def wrapper(cls):
        orig_vars = cls.__dict__.copy()
        slots = orig_vars.get('__slots__')
        if slots is not None:
            if isinstance(slots, str):
                slots = [slots]
            for slots_var in slots:
                orig_vars.pop(slots_var)
        orig_vars.pop('__dict__', None)
        orig_vars.pop('__weakref__', None)
        if hasattr(cls, '__qualname__'):
            orig_vars['__qualname__'] = cls.__qualname__
        return metaclass(cls.__name__, cls.__bases__, orig_vars)
    return wrapper


# TODO: Remove once Python 2.7 is dropped, use 'raise Blah() from err' instead.
# Slightly modified from six.reraise to makle calling it simpler for pyspnego and more like raise Excp() from err.
if sys.version_info[0] == 3:
    def reraise(exc, inner=None):
        exc.__cause__ = inner[1] or sys.exc_info()[1]
        raise exc

else:
    def _exec(_code_, _globs_=None, _locs_=None):
        """Execute code in a namespace."""
        frame = sys._getframe(1)
        _globs_ = frame.f_globals
        _locs_ = frame.f_locals
        del frame

        exec("""exec _code_ in _globs_, _locs_""")

    _exec("def reraise(exc, inner=None):\n    raise exc, None, inner[2] or sys.exc_info()[2]")

# TODO: Remove once Python 2.7 and 3.5 is dropped, use enum.IntFlag instead.
try:
    # IntFlag was added in Python 3.6.
    from enum import (
        Enum,
        IntEnum,
        IntFlag,
    )
except ImportError:
    # IntEnum is similar but the type is lost when using bitwise operations.
    from enum import (
        Enum,
        IntEnum,
    )
    IntFlag = IntEnum
