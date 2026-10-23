// Universal BGI / Ethornell image compatibility proxy & injector payload.
// Supports native BGI releases and Enigma-virtualized executable targets.
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

extern "C" int __cdecl DecodeJxlCBG(void*, size_t, const void*, size_t);

static HMODULE selfModule;
static INIT_ONCE once = INIT_ONCE_STATIC_INIT;
static void* originalDecoder;
static HRESULT (WINAPI *realInput)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);

static const unsigned char signature[8] = { 0x55, 0x8b, 0xec, 0xb8, 0xfc, 0x33, 0x00, 0x00 };

static void logline(const char* text) {
    WCHAR path[MAX_PATH];
    GetModuleFileNameW(selfModule, path, MAX_PATH);
    WCHAR* slash = wcsrchr(path, L'\\');
    if (!slash) return;
    wcscpy(slash + 1, L"bgi_jxl.log");
    HANDLE h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD n;
    WriteFile(h, text, (DWORD)strlen(text), &n, NULL);
    WriteFile(h, "\r\n", 2, &n, NULL);
    CloseHandle(h);
}

typedef int (__fastcall *ClassicDecoder)(void*, const unsigned char*);

static int __fastcall hookedDecode(void* out, const unsigned char* in) {
    if (in && !memcmp(in, "CompressedBG___", 16) && !memcmp(in + 32, "JXLCBG01", 8) && *(const uint16_t*)(in + 46) == 0x4a58) {
        logline("JXL hook entered v2: EDX=CBG file start");
        uint32_t payload = *(const uint32_t*)(in + 40);
        if (payload > 67108816) return (int)0x80000004;
        size_t len = 48 + (size_t)payload;
        uint16_t w = *(const uint16_t*)(in + 16), h = *(const uint16_t*)(in + 18);
        if (len > 67108864 || !w || !h || (size_t)w * h > 16777216) {
            logline("JXL envelope rejected: bounds");
            return (int)0x80000004;
        }
        int rc = DecodeJxlCBG(out, 16 + (size_t)w * h * 4, in, len);
        if (rc) {
            logline("JXL decode rejected");
            return (int)0x80000004;
        }
        logline("JXL image decoded through classic BGI ABI");
        return 0;
    }
    return ((ClassicDecoder)originalDecoder)(out, in);
}

extern "C" __declspec(dllexport) int __cdecl TestHookJxl(void* out, const unsigned char* in) {
    if (!in || memcmp(in + 32, "JXLCBG01", 8)) return -99;
    return hookedDecode(out, in);
}

static const unsigned char* fallbackExpected;
static int __fastcall fallbackProbe(void*, const unsigned char* in) { return in == fallbackExpected ? 1234 : -1234; }

extern "C" __declspec(dllexport) int __cdecl TestHookFallback() {
    unsigned char normal[64] = {};
    memcpy(normal, "CompressedBG___", 16);
    void* saved = originalDecoder;
    originalDecoder = (void*)&fallbackProbe;
    fallbackExpected = normal;
    int rc = hookedDecode(NULL, normal);
    originalDecoder = saved;
    return rc == 1234 ? 0 : -1;
}

extern "C" __declspec(dllexport) int __cdecl TestTrampoline() {
    unsigned char* code = (unsigned char*)VirtualAlloc(NULL, 64, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!code) return -1;
    memcpy(code, signature, 8);
    const unsigned char tail[] = { 0x89, 0xec, 0x5d, 0xc3 };
    memcpy(code + 8, tail, 4);
    unsigned char* tramp = code + 32;
    memcpy(tramp, code, 8);
    tramp[8] = 0xe9;
    int32_t r = (int32_t)((code + 8) - (tramp + 13));
    memcpy(tramp + 9, &r, 4);
    DWORD old;
    bool ok = VirtualProtect(code, 64, PAGE_EXECUTE_READ, &old) != 0;
    int ret = ok ? ((int(__cdecl*)())tramp)() : 0;
    VirtualFree(code, 0, MEM_RELEASE);
    return ret == 0x33fc ? 0 : -2;
}

static BOOL CALLBACK setup(PINIT_ONCE, PVOID, PVOID*) {
    unsigned char* base = (unsigned char*)GetModuleHandleW(NULL);
    if (!base) return TRUE;

    // Validate PE and check image bounds
    PIMAGE_DOS_HEADER dos = (PIMAGE_DOS_HEADER)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return TRUE;
    PIMAGE_NT_HEADERS nt = (PIMAGE_NT_HEADERS)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return TRUE;
    DWORD imgSize = nt->OptionalHeader.SizeOfImage;

    unsigned char* entry = NULL;

    // Check v1.00 (original release)
    if (0xc68e0 + 8 <= imgSize && !memcmp(base + 0xc68e0, signature, 8)) {
        entry = base + 0xc68e0;
        logline("Hook installing: target RVA C68E0 (v1.00)");
    }
    // Check known alternative build
    else if (0xc61f0 + 8 <= imgSize && !memcmp(base + 0xc61f0, signature, 8)) {
        entry = base + 0xc61f0;
        logline("Hook installing: target RVA C61F0");
    }
    // Cinderella mini fandisc: Japanese and Kuzushita Chinese builds.
    // Confirmed CompressedBG___ xref RVA 76F7E; fastcall ECX=out, EDX=in.
    else if (0x76f60 + 8 <= imgSize && !memcmp(base + 0x76f60, signature, 8)) {
        entry = base + 0x76f60;
        logline("Hook installing: target RVA 76F60 (Cinderella FD JP/CN)");
    }
    // Cinderella2 main game: Japanese and Kuzushita Chinese builds.
    // Confirmed CompressedBG___ xref RVA 77A2E; same classic decoder ABI.
    else if (0x77a10 + 8 <= imgSize && !memcmp(base + 0x77a10, signature, 8)) {
        entry = base + 0x77a10;
        logline("Hook installing: target RVA 77A10 (Cinderella2 JP/CN)");
    }
    // OshiRabu official Chinese release (SHA256 97168901...83262136).
    // Static CBG string/caller analysis confirms ECX=out, EDX=CBG start.
    else if (0x74ce0 + 8 <= imgSize && !memcmp(base + 0x74ce0, signature, 8)) {
        entry = base + 0x74ce0;
        logline("Hook installing: target RVA 74CE0 (OshiRabu)");
    }
    // Scan .text as fallback for custom builds or patches
    else if (imgSize > 0x1000) {
        DWORD scanEnd = (imgSize > 0x200000) ? 0x200000 : imgSize;
        for (size_t off = 0x1000; off + 8 <= scanEnd; ++off) {
            if (!memcmp(base + off, signature, 8)) {
                entry = base + off;
                char msg[64];
                snprintf(msg, sizeof(msg), "Hook installing: signature matched RVA %X", (unsigned int)off);
                logline(msg);
                break;
            }
        }
    }

    if (!entry) {
        // Packed builds may import this proxy before their engine code is expanded.
        // Leave INIT_ONCE incomplete so DirectInput8Create retries synchronously.
        logline("Hook deferred: decoder prologue not available");
        return FALSE;
    }

    unsigned char* tramp = (unsigned char*)VirtualAlloc(NULL, 13, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!tramp) return TRUE;
    memcpy(tramp, entry, 8);
    tramp[8] = 0xe9;
    int32_t rel = (int32_t)((entry + 8) - (tramp + 13));
    memcpy(tramp + 9, &rel, 4);
    DWORD old = 0;
    if (!VirtualProtect(tramp, 13, PAGE_EXECUTE_READ, &old)) {
        VirtualFree(tramp, 0, MEM_RELEASE);
        return TRUE;
    }
    originalDecoder = tramp;

    unsigned char patch[8] = { 0xe9, 0, 0, 0, 0, 0x90, 0x90, 0x90 };
    rel = (int32_t)((unsigned char*)&hookedDecode - (entry + 5));
    memcpy(patch + 1, &rel, 4);
    if (!VirtualProtect(entry, 8, PAGE_EXECUTE_READWRITE, &old)) {
        originalDecoder = NULL;
        VirtualFree(tramp, 0, MEM_RELEASE);
        return TRUE;
    }
    memcpy(entry, patch, 8);
    DWORD ignored;
    VirtualProtect(entry, 8, old, &ignored);
    FlushInstructionCache(GetCurrentProcess(), entry, 8);
    logline("Hook installed successfully.");
    return TRUE;
}

extern "C" __declspec(dllexport) HRESULT WINAPI DirectInput8Create(HINSTANCE instance, DWORD version, REFIID iid, LPVOID* out, LPUNKNOWN outer) {
    InitOnceExecuteOnce(&once, setup, NULL, NULL);
    if (!realInput) {
        WCHAR sys[MAX_PATH];
        UINT n = GetSystemDirectoryW(sys, MAX_PATH);
        if (n && n <= MAX_PATH - 15) {
            wcscat(sys, L"\\dinput8.dll");
            HMODULE m = LoadLibraryW(sys);
            if (m) realInput = (decltype(realInput))GetProcAddress(m, "DirectInput8Create");
        }
    }
    if (!realInput) return HRESULT_FROM_WIN32(ERROR_MOD_NOT_FOUND);
    return realInput(instance, version, iid, out, outer);
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD why, LPVOID) {
    if (why == DLL_PROCESS_ATTACH) {
        selfModule = module;
        DisableThreadLibraryCalls(module);
        InitOnceExecuteOnce(&once, setup, NULL, NULL);
    }
    return TRUE;
}
