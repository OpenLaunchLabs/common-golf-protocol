using System;
using System.Runtime.InteropServices;

// Deliberately small P/Invoke smoke. Full application wrappers should own the
// handle with SafeHandle and serialize operations on one worker thread.
static class Program
{
    const string Dll = "common_golf_protocol";
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    static extern uint cgp_abi_version();
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    static extern int cgp_create([MarshalAs(UnmanagedType.LPUTF8Str)] string config, out IntPtr endpoint);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    static extern int cgp_destroy(IntPtr endpoint);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    static extern IntPtr cgp_last_error();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate void Callback(IntPtr context, uint channel, IntPtr data, uint length);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    static extern int cgp_discover(IntPtr endpoint, uint ms, Callback callback, IntPtr context);
    static void Check(int code)
    {
        if (code < 0) throw new Exception(Marshal.PtrToStringUTF8(cgp_last_error()));
    }
    static int Main()
    {
        if (cgp_abi_version() != 1) throw new Exception("ABI mismatch");
        IntPtr endpoint;
        if (cgp_create("{}", out endpoint) != -1 || endpoint != IntPtr.Zero)
            throw new Exception("Invalid configuration was accepted");
        Check(cgp_create("{\"identity_directory\":\"cgp-ffi-identity/csharp\",\"role\":\"simulator\",\"info\":{\"type\":\"info\",\"name\":\"C# smoke\",\"protocol_version\":\"0.3.0\"}}", out endpoint));
        try
        {
            Exception callbackError = null;
            Callback callback = (context, channel, data, length) => {
                try
                {
                    var copy = new byte[checked((int)length)];
                    Marshal.Copy(data, copy, 0, copy.Length);
                    Console.WriteLine(System.Text.Encoding.UTF8.GetString(copy));
                }
                catch (Exception e) { callbackError = e; } // Never throw through native frames.
            };
            Check(cgp_discover(endpoint, 100, callback, IntPtr.Zero));
            GC.KeepAlive(callback);
            if (callbackError != null) throw callbackError;
        }
        finally { Check(cgp_destroy(endpoint)); }
        return 0;
    }
}
