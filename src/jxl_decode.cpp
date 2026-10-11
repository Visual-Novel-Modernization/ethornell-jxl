// Independent x86 decoder and classic CBG replacement ABI. Supports 24bpp & 32bpp.
#include <windows.h>
#include <jxl/decode.h>
#include <stdint.h>
#include <stdio.h>
#include <vector>
#include <string.h>

// Envelope keeps engine-readable CBG metadata in bytes16..31.
// CBG magic16, original metadata16, JXLC tag8 @32, payload u32@40,
// reserved checksum u16@44, version u16@46=0x4a58 (classic dispatch), JXL @48.
static const unsigned char tag[8]={'J','X','L','C','B','G','0','1'};
static uint16_t u16(const unsigned char*p){return p[0]|p[1]<<8;}
static uint32_t u32(const unsigned char*p){return p[0]|p[1]<<8|p[2]<<16|(uint32_t)p[3]<<24;}

extern "C" __declspec(dllexport) int __cdecl DecodeJxlCBG(void* output,size_t capacity,const void* input,size_t length){
  const unsigned char* p=(const unsigned char*)input;
  if(!output||!input||length<48||memcmp(p,"CompressedBG___",16)||memcmp(p+32,tag,8)||u16(p+46)!=0x4a58) return -1;
  const size_t w=u16(p+16),h=u16(p+18);const unsigned bpp=u16(p+20);
  if(!w||!h||(bpp!=24&&bpp!=32)||u16(p+22)>1||u32(p+40)!=length-48||w*h>16777216||capacity<16+w*h*4) return -2;
  JxlDecoder* d=JxlDecoderCreate(NULL);if(!d)return -3;
  int result=-4;bool info=false,frame=false;
  JxlBasicInfo bi={};
  uint32_t num_ch=(bpp==32)?4:3;
  JxlPixelFormat fmt={num_ch,JXL_TYPE_UINT8,JXL_NATIVE_ENDIAN,0};
  std::vector<unsigned char> pix;
  if(JxlDecoderSubscribeEvents(d,JXL_DEC_BASIC_INFO|JXL_DEC_FULL_IMAGE)!=JXL_DEC_SUCCESS)goto done;
  JxlDecoderSetInput(d,p+48,length-48);JxlDecoderCloseInput(d);
  for(;;){
    JxlDecoderStatus s=JxlDecoderProcessInput(d);
    if(s==JXL_DEC_BASIC_INFO){
      if(JxlDecoderGetBasicInfo(d,&bi)!=JXL_DEC_SUCCESS||bi.xsize!=w||bi.ysize!=h||bi.bits_per_sample!=8||bi.have_animation||bi.orientation!=JXL_ORIENT_IDENTITY)break;
      if(bpp==24&&(bi.num_color_channels!=3||bi.alpha_bits!=0))break;
      if(bpp==32&&(bi.num_color_channels!=3))break;
      info=true;
    }else if(s==JXL_DEC_NEED_IMAGE_OUT_BUFFER){
      size_t bytes=0;if(!info||JxlDecoderImageOutBufferSize(d,&fmt,&bytes)!=JXL_DEC_SUCCESS||bytes!=w*h*num_ch)break;
      pix.resize(bytes);if(JxlDecoderSetImageOutBuffer(d,&fmt,pix.data(),bytes)!=JXL_DEC_SUCCESS)break;
    }else if(s==JXL_DEC_FULL_IMAGE){if(frame)break;frame=true;
    }else if(s==JXL_DEC_SUCCESS){
      if(!info||!frame||pix.size()!=w*h*num_ch)break;
      unsigned char* o=(unsigned char*)output;memcpy(o,p+16,16);
      if(bpp==24){
        o[4]=32;o[5]=0;o[8]=7;o[9]=0;
        for(size_t i=0;i<w*h;i++){
          o[16+4*i+0]=pix[3*i+2];o[16+4*i+1]=pix[3*i+1];o[16+4*i+2]=pix[3*i+0];o[16+4*i+3]=0;
        }
      }else{
        for(size_t i=0;i<w*h;i++){
          o[16+4*i+0]=pix[4*i+2];o[16+4*i+1]=pix[4*i+1];o[16+4*i+2]=pix[4*i+0];o[16+4*i+3]=pix[4*i+3];
        }
      }
      result=0;break;
    }else break;
  }
done:JxlDecoderDestroy(d);return result;
}

#ifdef DECODER_TEST
int main(int argc,char**argv){
  if(argc!=3){fprintf(stderr,"usage: jxl_decode_test envelope output.raw\n");return 1;}
  FILE*f=fopen(argv[1],"rb");if(!f)return 2;fseek(f,0,SEEK_END);long size=ftell(f);rewind(f);if(size<48||size>67108864){fclose(f);return 3;}
  std::vector<unsigned char> in(size);if(fread(in.data(),1,size,f)!=(size_t)size){fclose(f);return 4;}fclose(f);
  size_t bytes=16+(size_t)u16(in.data()+16)*u16(in.data()+18)*4;std::vector<unsigned char> out(bytes);
  int rc=DecodeJxlCBG(out.data(),out.size(),in.data(),in.size());printf("libjxl=%u decode=%d bytes=%u\n",JxlDecoderVersion(),rc,(unsigned)bytes);if(rc)return 5;
  f=fopen(argv[2],"wb");if(!f)return 6;bool ok=fwrite(out.data(),1,out.size(),f)==out.size();fclose(f);return ok?0:7;
}
#endif
