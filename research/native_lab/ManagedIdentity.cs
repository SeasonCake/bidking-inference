// Independent self-owned managed build identity control, not an application plugin.
using System;
using System.Reflection;
public static class ManagedIdentity {
    public static int Main() {
#if VARIANT_B
        const int variant = 1;
#else
        const int variant = 0;
#endif
        Console.WriteLine("{\"synthetic\":true,\"variant\":" + variant
            + ",\"mvid\":\"" + typeof(ManagedIdentity).Module.ModuleVersionId.ToString() + "\"}");
        return 0;
    }
}
