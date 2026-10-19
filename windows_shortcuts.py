"""Create Shell Links through the Unicode COM interfaces supplied by Windows."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import uuid


class GUID(ctypes.Structure):
    _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def checked(result):
    if result < 0:
        raise ctypes.WinError(result)


def method(pointer, index, *argument_types):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)(table[index])


def create_shortcut(path, target, arguments, working_directory, icon):
    """Use IShellLinkW and IPersistFile; WScript.Shell.Save loses Unicode on some locales."""
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    result = ole.CoInitializeEx(None, 2)
    # A caller may already have initialized this thread with another COM apartment model.
    initialized = result >= 0
    if result != -2147417850:  # RPC_E_CHANGED_MODE
        checked(result)
    link, persist = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid = GUID.parse("00021401-0000-0000-c000-000000000046")
        iid_link = GUID.parse("000214f9-0000-0000-c000-000000000046")
        checked(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid_link), ctypes.byref(link)))
        for index, value in ((20, target), (11, arguments), (9, working_directory), (7, "Bambu Studio profile")):
            checked(method(link, index, wintypes.LPCWSTR)(link, str(value)))
        checked(method(link, 17, wintypes.LPCWSTR, ctypes.c_int)(link, str(icon), 0))
        iid_persist = GUID.parse("0000010b-0000-0000-c000-000000000046")
        checked(method(link, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            link, ctypes.byref(iid_persist), ctypes.byref(persist)))
        checked(method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(persist, str(Path(path).absolute()), True))
    finally:
        if persist.value:
            method(persist, 2)(persist)
        if link.value:
            method(link, 2)(link)
        if initialized:
            ole.CoUninitialize()
