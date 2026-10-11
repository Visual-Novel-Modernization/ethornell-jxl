// Universal launcher for BGI / Ethornell games with JXL hook
// Sequential main executable detection with optional launcher.ini configuration

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <tlhelp32.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include <vector>
#include <string>
#include <algorithm>

static void ReportError(const wchar_t* msg, const wchar_t* title) {
    wprintf(L"Error: %ls\n", msg);
    MessageBoxW(NULL, msg, title, MB_ICONERROR | MB_OK);
}

static bool FileExists(const wchar_t* path) {
    DWORD attr = GetFileAttributesW(path);
    return (attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY));
}

static void BuildPath(wchar_t* out, size_t maxLen, const wchar_t* dir, const wchar_t* file) {
    wcsncpy(out, dir, maxLen - 1);
    out[maxLen - 1] = L'\0';
    size_t len = wcslen(out);
    if (len > 0 && out[len - 1] != L'\\' && len + 1 < maxLen) {
        out[len] = L'\\';
        out[len + 1] = L'\0';
    }
    wcsncat(out, file, maxLen - wcslen(out) - 1);
}

static bool FindHookDll(wchar_t* outPath, size_t maxLen, const wchar_t* appDir) {
    // 1. Check in launcher directory
    BuildPath(outPath, maxLen, appDir, L"bgi_jxl_hook.dll");
    if (FileExists(outPath)) return true;

    BuildPath(outPath, maxLen, appDir, L"dinput8.dll");
    if (FileExists(outPath)) return true;

    // 2. Check in current working directory
    WCHAR curDir[MAX_PATH];
    GetCurrentDirectoryW(MAX_PATH, curDir);
    BuildPath(outPath, maxLen, curDir, L"bgi_jxl_hook.dll");
    if (FileExists(outPath)) return true;

    BuildPath(outPath, maxLen, curDir, L"dinput8.dll");
    if (FileExists(outPath)) return true;

    return false;
}

static bool IsExcludedExe(const wchar_t* filename, const wchar_t* selfExeName) {
    if (_wcsicmp(filename, selfExeName) == 0) return true;

    std::wstring lower = filename;
    std::transform(lower.begin(), lower.end(), lower.begin(), ::towlower);

    const wchar_t* excludedKeywords[] = {
        L"launcher",
        L"hook",
        L"setup",
        L"install",
        L"unins",
        L"config",
        L"cfg",
        L"update",
        L"test",
        L"inject",
        L"crash",
        L"report",
        L"tool",
        L"dump",
        L"arc20",
        L"repack"
    };

    for (const wchar_t* kw : excludedKeywords) {
        if (lower.find(kw) != std::wstring::npos) {
            return true;
        }
    }
    return false;
}

static bool IsEnigmaVirtualBox(const wchar_t* exePath) {
    HANDLE h = CreateFileW(exePath, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return false;

    DWORD read = 0;
    unsigned char buf[4096];
    bool isEnigma = false;
    if (ReadFile(h, buf, sizeof(buf), &read, NULL) && read > 0x100) {
        DWORD e_lfanew = *(DWORD*)(buf + 0x3c);
        if (e_lfanew + 0x18 < read && *(DWORD*)(buf + e_lfanew) == 0x00004550) {
            WORD numSec = *(WORD*)(buf + e_lfanew + 6);
            WORD optHdrSize = *(WORD*)(buf + e_lfanew + 0x14);
            DWORD secOff = e_lfanew + 0x18 + optHdrSize;
            SetFilePointer(h, secOff, NULL, FILE_BEGIN);
            unsigned char secBuf[40 * 16];
            if (ReadFile(h, secBuf, sizeof(secBuf), &read, NULL)) {
                for (DWORD i = 0; i < numSec && i < 16; ++i) {
                    if (!memcmp(secBuf + i * 40, ".enigma", 7)) {
                        isEnigma = true;
                        break;
                    }
                }
            }
        }
    }
    CloseHandle(h);
    return isEnigma;
}

static std::wstring FindEnigmaMilestoneDll(const wchar_t* appDir) {
    // 1. Check launcher.ini: [Launcher] MilestoneDll or EnigmaDll
    WCHAR iniPath[MAX_PATH];
    BuildPath(iniPath, MAX_PATH, appDir, L"launcher.ini");
    if (FileExists(iniPath)) {
        WCHAR iniDll[MAX_PATH] = {0};
        GetPrivateProfileStringW(L"Launcher", L"MilestoneDll", L"", iniDll, MAX_PATH, iniPath);
        if (!iniDll[0]) {
            GetPrivateProfileStringW(L"Launcher", L"EnigmaDll", L"", iniDll, MAX_PATH, iniPath);
        }
        if (iniDll[0]) return iniDll;
    }

    // 2. Scan appDir for companion DLLs
    WCHAR pattern[MAX_PATH];
    BuildPath(pattern, MAX_PATH, appDir, L"*.dll");
    WIN32_FIND_DATAW fd;
    HANDLE hFind = FindFirstFileW(pattern, &fd);
    if (hFind != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
                std::wstring name = fd.cFileName;
                std::wstring lower = name;
                std::transform(lower.begin(), lower.end(), lower.begin(), ::towlower);
                // Exclude hook, proxy, and standard runtime libraries
                if (lower == L"bgi_jxl_hook.dll" || lower == L"dinput8.dll" || lower.rfind(L"lib", 0) == 0) {
                    continue;
                }
                CloseHandle(hFind);
                return name;
            }
        } while (FindNextFileW(hFind, &fd));
        CloseHandle(hFind);
    }
    return L"";
}

static bool FindTargetExe(const wchar_t* userArg, wchar_t* outExe, size_t maxLen, bool* isEnigma, const wchar_t* appDir) {
    // 1. Explicit command line argument
    if (userArg && userArg[0] && FileExists(userArg)) {
        wcsncpy(outExe, userArg, maxLen - 1);
        outExe[maxLen - 1] = L'\0';
        *isEnigma = IsEnigmaVirtualBox(outExe);
        return true;
    }

    // 2. Configuration file: launcher.ini
    WCHAR iniPath[MAX_PATH];
    BuildPath(iniPath, MAX_PATH, appDir, L"launcher.ini");
    if (FileExists(iniPath)) {
        WCHAR iniTarget[MAX_PATH] = {0};
        GetPrivateProfileStringW(L"Launcher", L"Target", L"", iniTarget, MAX_PATH, iniPath);
        if (!iniTarget[0]) {
            GetPrivateProfileStringW(L"Launcher", L"TargetExe", L"", iniTarget, MAX_PATH, iniPath);
        }
        if (iniTarget[0]) {
            if (FileExists(iniTarget)) {
                wcsncpy(outExe, iniTarget, maxLen - 1);
                outExe[maxLen - 1] = L'\0';
                *isEnigma = IsEnigmaVirtualBox(outExe);
                return true;
            }
            BuildPath(outExe, maxLen, appDir, iniTarget);
            if (FileExists(outExe)) {
                *isEnigma = IsEnigmaVirtualBox(outExe);
                return true;
            }
        }
    }

    // 3. Sequential detection of main executable candidates in appDir
    WCHAR selfPath[MAX_PATH];
    GetModuleFileNameW(NULL, selfPath, MAX_PATH);
    const wchar_t* selfName = wcsrchr(selfPath, L'\\');
    selfName = selfName ? selfName + 1 : selfPath;

    WCHAR searchPattern[MAX_PATH];
    BuildPath(searchPattern, MAX_PATH, appDir, L"*.exe");

    struct Candidate {
        std::wstring path;
        int priority;
        uint64_t fileSize;
    };
    std::vector<Candidate> candidates;

    WIN32_FIND_DATAW fd;
    HANDLE hFind = FindFirstFileW(searchPattern, &fd);
    if (hFind != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
                if (!IsExcludedExe(fd.cFileName, selfName)) {
                    WCHAR fullPath[MAX_PATH];
                    BuildPath(fullPath, MAX_PATH, appDir, fd.cFileName);
                    uint64_t fsz = ((uint64_t)fd.nFileSizeHigh << 32) | fd.nFileSizeLow;

                    int priority = 10;
                    std::wstring lower = fd.cFileName;
                    std::transform(lower.begin(), lower.end(), lower.begin(), ::towlower);

                    // Sequential priority: standard BGI engine executable names first
                    if (lower == L"bgi.exe") {
                        priority = 100;
                    } else if (lower.find(L"bgi") != std::wstring::npos) {
                        priority = 80;
                    } else if (lower.find(L"ethornell") != std::wstring::npos) {
                        priority = 70;
                    }

                    candidates.push_back({ fullPath, priority, fsz });
                }
            }
        } while (FindNextFileW(hFind, &fd));
        CloseHandle(hFind);
    }

    if (!candidates.empty()) {
        // Sort sequentially: highest priority first, then largest file size
        std::sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
            if (a.priority != b.priority) return a.priority > b.priority;
            return a.fileSize > b.fileSize;
        });

        wcsncpy(outExe, candidates[0].path.c_str(), maxLen - 1);
        outExe[maxLen - 1] = L'\0';
        *isEnigma = IsEnigmaVirtualBox(outExe);
        return true;
    }

    return false;
}

static bool RemoteCallW(HANDLE hProcess, FARPROC pfn, const wchar_t* arg) {
    if (!pfn) return false;
    size_t sizeBytes = (wcslen(arg) + 1) * sizeof(wchar_t);
    void* remoteMem = VirtualAllocEx(hProcess, NULL, sizeBytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remoteMem) return false;

    if (!WriteProcessMemory(hProcess, remoteMem, arg, sizeBytes, NULL)) {
        VirtualFreeEx(hProcess, remoteMem, 0, MEM_RELEASE);
        return false;
    }

    HANDLE hThread = CreateRemoteThread(hProcess, NULL, 0, (LPTHREAD_START_ROUTINE)(void*)pfn, remoteMem, 0, NULL);
    if (!hThread) {
        VirtualFreeEx(hProcess, remoteMem, 0, MEM_RELEASE);
        return false;
    }

    WaitForSingleObject(hThread, 8000);
    DWORD exitCode = 0;
    GetExitCodeThread(hThread, &exitCode);
    CloseHandle(hThread);
    VirtualFreeEx(hProcess, remoteMem, 0, MEM_RELEASE);
    return (exitCode != 0);
}

static bool InjectDll(HANDLE hProcess, const wchar_t* dllPath, const wchar_t* dllDir) {
    HMODULE hKernel = GetModuleHandleW(L"kernel32.dll");
    if (!hKernel) return false;

    // Set DLL search directory in target process so dependencies are located
    FARPROC pSetDllDir = GetProcAddress(hKernel, "SetDllDirectoryW");
    if (pSetDllDir && dllDir && dllDir[0]) {
        RemoteCallW(hProcess, pSetDllDir, dllDir);
    }

    FARPROC pLoadLib = GetProcAddress(hKernel, "LoadLibraryW");
    return RemoteCallW(hProcess, pLoadLib, dllPath);
}

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, PWSTR pCmdLine, int nCmdShow) {
    (void)hInstance; (void)hPrevInstance; (void)pCmdLine; (void)nCmdShow;
    AttachConsole(ATTACH_PARENT_PROCESS);

    WCHAR appDir[MAX_PATH];
    GetModuleFileNameW(NULL, appDir, MAX_PATH);
    WCHAR* slash = wcsrchr(appDir, L'\\');
    if (slash) *slash = L'\0';

    WCHAR hookDll[MAX_PATH];
    if (!FindHookDll(hookDll, MAX_PATH, appDir)) {
        WCHAR msg[512];
        _snwprintf(msg, 512, L"Hook DLL not found (bgi_jxl_hook.dll or dinput8.dll).\nSearch directory: %ls", appDir);
        ReportError(msg, L"BGI JXL Launcher");
        return 1;
    }

    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    const wchar_t* userTarget = (argc > 1) ? argv[1] : NULL;

    WCHAR targetExe[MAX_PATH];
    bool isEnigma = false;
    if (!FindTargetExe(userTarget, targetExe, MAX_PATH, &isEnigma, appDir)) {
        WCHAR msg[512];
        _snwprintf(msg, 512, L"Game executable not found.\nConfigure launcher.ini:\n[Launcher]\nTarget=game.exe\nSearch directory: %ls", appDir);
        ReportError(msg, L"BGI JXL Launcher");
        if (argv) LocalFree(argv);
        return 2;
    }
    if (argv) LocalFree(argv);

    wprintf(L"Target: %ls (Enigma=%d)\n", targetExe, isEnigma);
    wprintf(L"Hook DLL: %ls\n", hookDll);

    WCHAR workDir[MAX_PATH];
    wcscpy(workDir, targetExe);
    WCHAR* targetSlash = wcsrchr(workDir, L'\\');
    if (targetSlash) *targetSlash = L'\0';

    STARTUPINFOW si;
    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi;
    memset(&pi, 0, sizeof(pi));

    if (isEnigma) {
        // Enigma Virtual Box: run normally and synchronize injection
        if (!CreateProcessW(targetExe, NULL, NULL, NULL, FALSE, 0, NULL, workDir, &si, &pi)) {
            ReportError(L"Failed to launch game executable.", L"BGI JXL Launcher");
            return 3;
        }

        std::wstring milestoneDll = FindEnigmaMilestoneDll(appDir);
        const wchar_t* targetExeName = wcsrchr(targetExe, L'\\');
        targetExeName = targetExeName ? targetExeName + 1 : targetExe;

        bool detected = false;
        DWORD startTick = GetTickCount();
        wprintf(L"Waiting for Enigma Virtual Box initialization (milestone: %ls)...\n",
                milestoneDll.empty() ? targetExeName : milestoneDll.c_str());

        while (GetTickCount() - startTick < 15000) {
            DWORD exitCode = 0;
            if (GetExitCodeProcess(pi.hProcess, &exitCode) && exitCode != STILL_ACTIVE) {
                break;
            }

            HANDLE hSnap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pi.dwProcessId);
            if (hSnap != INVALID_HANDLE_VALUE) {
                MODULEENTRY32W me;
                memset(&me, 0, sizeof(me));
                me.dwSize = sizeof(me);
                if (Module32FirstW(hSnap, &me)) {
                    do {
                        if (!milestoneDll.empty()) {
                            if (_wcsicmp(me.szModule, milestoneDll.c_str()) == 0) {
                                detected = true;
                                break;
                            }
                        } else {
                            if (_wcsicmp(me.szModule, targetExeName) == 0) {
                                detected = true;
                                break;
                            }
                        }
                    } while (Module32NextW(hSnap, &me));
                }
                CloseHandle(hSnap);
            }

            if (detected) break;
            Sleep(5);
        }

        if (!detected) {
            ReportError(L"Timed out waiting for Enigma Virtual Box initialization.", L"BGI JXL Launcher");
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
            return 4;
        }

        // Suspend primary thread to freeze the game right after Enigma unpacks, before image decode
        SuspendThread(pi.hThread);
        wprintf(L"Enigma ready. Injecting JXL Hook...\n");

        bool ok = InjectDll(pi.hProcess, hookDll, workDir);
        ResumeThread(pi.hThread);

        if (!ok) {
            ReportError(L"JXL Hook DLL injection failed (LoadLibraryW returned NULL).", L"BGI JXL Launcher");
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
            return 5;
        }
        wprintf(L"JXL Hook injected successfully!\n");
    } else {
        // Native release: launch suspended, inject immediately, then resume
        if (!CreateProcessW(targetExe, NULL, NULL, NULL, FALSE, CREATE_SUSPENDED, NULL, workDir, &si, &pi)) {
            ReportError(L"Failed to launch game executable.", L"BGI JXL Launcher");
            return 3;
        }

        wprintf(L"Process suspended. Injecting JXL Hook...\n");
        bool ok = InjectDll(pi.hProcess, hookDll, workDir);
        ResumeThread(pi.hThread);

        if (!ok) {
            ReportError(L"JXL Hook DLL injection failed.", L"BGI JXL Launcher");
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
            return 5;
        }
        wprintf(L"JXL Hook injected successfully!\n");
    }

    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
