// Offline regression: executes the same patch/trampoline writers as the proxy.
#include "../src/dinput_proxy64.cpp"
#include <vector>
int main(int argc, char** argv) {
    auto code = (unsigned char*)VirtualAlloc(NULL, 4096, MEM_COMMIT|MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!code) return 1;
    memcpy(code, decoderPrologue, kStolen);
    // Restore R14 pushed by the real prologue, return its EAX value.
    code[12]=0x41; code[13]=0x5E; code[14]=0xC3;
    using Fixture = int (*)(void*, const unsigned char*);
    unsigned char input[48]={};
    int baseline=((Fixture)code)(nullptr,input);
    memcpy(code+128,code,kStolen);
    writeReturnJump(code+128+kStolen,code+kStolen);
    originalDecoder=code+128;
    writeEntryJump(code,(const void*)&hookedDecode);
    FlushInstructionCache(GetCurrentProcess(),code,4096);
    int patched=((Fixture)code)(nullptr,input);
    printf("baseline=%X patched_fallback=%X expected=3420\n",baseline,patched);
    if(baseline!=0x3420 || patched!=0x3420) return 2;
    // Old RAX return jump must reproduce the regression.
    writeEntryJump(code+128+kStolen,code+kStolen);
    FlushInstructionCache(GetCurrentProcess(),code,4096);
    int broken=((Fixture)code)(nullptr,input);
    printf("old_return=%X differs=%d\n",broken,broken!=0x3420);
    if(broken==0x3420) return 3;
    writeReturnJump(code+128+kStolen,code+kStolen);
    originalDecoder=nullptr;
    VirtualFree(code,0,MEM_RELEASE);
    if(argc>1){
        FILE* f=fopen(argv[1],"rb"); if(!f) return 4;
        fseek(f,0,SEEK_END); long n=ftell(f); rewind(f);
        std::vector<unsigned char> in(n); if(fread(in.data(),1,n,f)!=(size_t)n) return 5; fclose(f);
        uint16_t wh[2]; memcpy(wh,in.data()+16,4);
        std::vector<unsigned char> out(16+(size_t)wh[0]*wh[1]*4+32,0xCD);
        int rc=TestHookJxl(out.data(),in.data());
        bool guard=true; for(size_t i=out.size()-32;i<out.size();++i) guard &= out[i]==0xCD;
        printf("JXL_hook_result=%d output_guard=%d\n",rc,guard);
        if(rc || !guard) return 6;
    }
    puts("PASS"); return 0;
}
