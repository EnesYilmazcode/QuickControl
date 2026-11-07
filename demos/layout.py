"""Place only the local demo window before timing or recording begins."""
import ctypes
from ctypes import wintypes


def activate(handle):
    """Focus a previously identified fixture HWND before input is measured."""
    user32=ctypes.WinDLL('user32',use_last_error=True)
    kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
    user32.GetForegroundWindow.restype=wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype=wintypes.DWORD
    user32.AttachThreadInput.argtypes=[wintypes.DWORD,wintypes.DWORD,wintypes.BOOL]
    user32.SetForegroundWindow.argtypes=[wintypes.HWND]
    user32.SetForegroundWindow.restype=wintypes.BOOL
    user32.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint]
    message=wintypes.MSG()
    user32.PeekMessageW(ctypes.byref(message),None,0,0,0)
    current=kernel32.GetCurrentThreadId()
    foreground=user32.GetForegroundWindow()
    threads={user32.GetWindowThreadProcessId(handle,None)}
    if foreground: threads.add(user32.GetWindowThreadProcessId(foreground,None))
    attached=[]
    try:
        for thread in threads:
            if thread and thread!=current and user32.AttachThreadInput(current,thread,True):
                attached.append(thread)
        user32.SetForegroundWindow(handle)
    finally:
        for thread in reversed(attached): user32.AttachThreadInput(current,thread,False)
    if user32.GetForegroundWindow()!=handle:
        raise RuntimeError('Synthetic fixture did not receive foreground focus; no input should run')


def assert_foreground(handle):
    user32=ctypes.WinDLL('user32',use_last_error=True)
    user32.GetForegroundWindow.restype=wintypes.HWND
    if user32.GetForegroundWindow()!=handle:
        raise RuntimeError('Synthetic fixture lost foreground focus; no input was started')


def position_fixture(title='QuickControl · Real GUI speed lab'):
    user32=ctypes.WinDLL('user32', use_last_error=True)
    user32.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR]
    user32.FindWindowW.restype=wintypes.HWND
    user32.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint]
    user32.SetWindowPos.restype=wintypes.BOOL
    handle=user32.FindWindowW('Chrome_WidgetWin_1',title)
    if not handle: raise RuntimeError('Synthetic fixture window not found')
    if not user32.SetWindowPos(handle,0,60,60,1240,900,0x0040):
        raise ctypes.WinError(ctypes.get_last_error())
    activate(handle)
    return handle


if __name__ == '__main__':
    position_fixture()
