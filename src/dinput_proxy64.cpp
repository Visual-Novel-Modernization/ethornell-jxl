// 64-bit BGI / Ethornell DirectInput8 proxy with JXL CBG interception.
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern "C" int __cdecl DecodeJxlCBG(void*, size_t, const void*, size_t);

static HMODULE selfModule;
static INIT_ONCE once = INIT_ONCE_STATIC_INIT;
static void* originalDecoder;
static HRESULT (WINAPI *realInput)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);

static const unsigned char decoderPrologue[12] = {
    0x48, 0x89, 0x74, 0x24, 0x20, 0x41, 0x56, 0xB8, 0x20, 0x34, 0x00, 0x00
};
static constexpr size_t kStolen = sizeof(decoderPrologue);
static constexpr size_t kJumpSize = 12;

static void logline(const char* text) {
    WCHAR path[MAX_PATH];
    GetModuleFileNameW(selfModule, path, MAX_PATH);
    WCHAR* slash = wcsrchr(path, L'\\');
    if (!slash) return;
    wcscpy(slash + 1, L"bgi_jxl.log");
    HANDLE h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    WCHAR exePath[MAX_PATH] = L"";
    GetModuleFileNameW(NULL, exePath, MAX_PATH);
    WCHAR* exeName = wcsrchr(exePath, L'\\');
    exeName = exeName ? exeName + 1 : exePath;
    char prefix[160];
    snprintf(prefix, sizeof(prefix), "[PID %lu %ls] ", GetCurrentProcessId(), exeName);
    DWORD n;
    WriteFile(h, prefix, (DWORD)strlen(prefix), &n, NULL);
    WriteFile(h, text, (DWORD)strlen(text), &n, NULL);
    WriteFile(h, "\r\n", 2, &n, NULL);
    CloseHandle(h);
}

// Microsoft x64 ABI: RCX is the output buffer and RDX is the CBG input.
typedef int (*ClassicDecoder)(void*, const unsigned char*);
static bool readableRange(const void* ptr, size_t size) {
    if (!ptr || !size) return false;
    uintptr_t begin = (uintptr_t)ptr, end = begin + size;
    if (end < begin) return false;
    while (begin < end) {
        MEMORY_BASIC_INFORMATION mbi = {};
        if (!VirtualQuery((const void*)begin, &mbi, sizeof(mbi))) return false;
        if (mbi.State != MEM_COMMIT || (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD))) return false;
        uintptr_t regionEnd = (uintptr_t)mbi.BaseAddress + mbi.RegionSize;
        if (regionEnd <= begin) return false;
        begin = regionEnd;
    }
    return true;
}

static bool isJxlEnvelope(const unsigned char* in) {
    if (!readableRange(in, 48)) return false;
    if (memcmp(in, "CompressedBG___", 16) != 0) return false;
    if (memcmp(in + 32, "JXLCBG01", 8) != 0) return false;
    uint16_t version;
    memcpy(&version, in + 46, sizeof(version));
    return version == 0x4a58;
}

static int hookedDecode(void* out, const unsigned char* in) {
    if (isJxlEnvelope(in)) {
        uint32_t payload, w, h, bpp;
        memcpy(&payload, in + 40, sizeof(payload));
        uint16_t whb[3];
        memcpy(whb, in + 16, sizeof(whb));
        w = whb[0]; h = whb[1]; bpp = whb[2];
        size_t len = 48 + (size_t)payload;
        if (payload > 67108816 || len > 67108864 || !w || !h || (size_t)w * h > 67108864) {
            logline("JXL envelope rejected: bounds");
            return (int)0x80000004;
        }
        size_t cap = 16 + (size_t)w * h * (bpp == 8 ? 1 : 4);
        int rc = DecodeJxlCBG(out, cap, in, len);
        if (!rc) {
            logline("JXL image decoded through x64 classic BGI ABI");
            return 0;
        }
        logline("JXL decode rejected");
        return (int)0x80000004;
    }
    return originalDecoder ? ((ClassicDecoder)originalDecoder)(out, in) : (int)0x80000004;
}

extern "C" __declspec(dllexport) int __cdecl TestHookJxl(void* out, const unsigned char* in) {
    if (!isJxlEnvelope(in)) return -99;
    return hookedDecode(out, in);
}

extern "C" __declspec(dllexport) int __cdecl TestHookFallback(void) {
    unsigned char bad[8] = {0};
    return isJxlEnvelope(bad) ? -1 : 0;
}

static void writeEntryJump(unsigned char* at, const void* target) {
    // mov rax, imm64; jmp rax — exactly 12 bytes, preserving the entry boundary.
    at[0] = 0x48; at[1] = 0xB8;
    uint64_t address = (uint64_t)(uintptr_t)target;
    memcpy(at + 2, &address, sizeof(address));
    at[10] = 0xFF; at[11] = 0xE0;
}

static void writeReturnJump(unsigned char* at, const void* target) {
    // RIP-indirect jump: 14 bytes in our allocated trampoline, preserving every register.
    at[0] = 0xFF; at[1] = 0x25;
    memset(at + 2, 0, 4);
    uint64_t address = (uint64_t)(uintptr_t)target;
    memcpy(at + 6, &address, sizeof(address));
}

static BOOL CALLBACK setup(PINIT_ONCE, PVOID, PVOID*) {
    unsigned char* base = (unsigned char*)GetModuleHandleW(NULL);
    if (!base) return TRUE;
    PIMAGE_DOS_HEADER dos = (PIMAGE_DOS_HEADER)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return TRUE;
    PIMAGE_NT_HEADERS64 nt = (PIMAGE_NT_HEADERS64)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) {
        logline("Hook disabled: target is not PE32+");
        return TRUE;
    }
    DWORD imgSize = nt->OptionalHeader.SizeOfImage;
    unsigned char* entry = NULL;
    if (0xC00C0 + kStolen <= imgSize && !memcmp(base + 0xC00C0, decoderPrologue, kStolen)) {
        entry = base + 0xC00C0;
        logline("Hook target RVA C00C0 (64-bit FD)");
    } else if (0xBF7C0 + kStolen <= imgSize && !memcmp(base + 0xBF7C0, decoderPrologue, kStolen)) {
        entry = base + 0xBF7C0;
        logline("Hook target RVA BF7C0 (64-bit Triple Pairing)");
    } else {
        for (size_t off = 0x1000; off + kStolen <= imgSize; ++off) {
            if (!memcmp(base + off, decoderPrologue, kStolen)) {
                entry = base + off;
                char msg[96];
                snprintf(msg, sizeof(msg), "Hook target signature RVA %X", (unsigned)off);
                logline(msg);
                break;
            }
        }
    }
    if (!entry) {
        logline("Hook disabled: x64 decoder prologue mismatch");
        return TRUE;
    }

    // Complete 12-byte prologue followed by a register-preserving return jump.
    static constexpr size_t kReturnSize = 14;
    unsigned char* tramp = (unsigned char*)VirtualAlloc(NULL, kStolen + kReturnSize,
        MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!tramp) return TRUE;
    memcpy(tramp, entry, kStolen);
    writeReturnJump(tramp + kStolen, entry + kStolen);
    DWORD old = 0;
    if (!VirtualProtect(tramp, kStolen + kReturnSize, PAGE_EXECUTE_READ, &old)) {
        VirtualFree(tramp, 0, MEM_RELEASE);
        return TRUE;
    }
    originalDecoder = tramp;

    DWORD entryOld = 0;
    if (!VirtualProtect(entry, kJumpSize, PAGE_EXECUTE_READWRITE, &entryOld)) {
        originalDecoder = NULL;
        VirtualFree(tramp, 0, MEM_RELEASE);
        return TRUE;
    }
    unsigned char patch[kJumpSize];
    writeEntryJump(patch, (const void*)&hookedDecode);
    memcpy(entry, patch, sizeof(patch));
    DWORD ignored;
    VirtualProtect(entry, kJumpSize, entryOld, &ignored);
    FlushInstructionCache(GetCurrentProcess(), entry, kJumpSize);
    logline("x64 absolute hook installed successfully");
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
    }
    return TRUE;
}
