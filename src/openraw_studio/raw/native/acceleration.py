"""Optional OpenCL and compiled CPU rendering with a NumPy reference fallback."""

from __future__ import annotations

import os
import threading

import numpy as np

from openraw_studio.raw.native.tonal import apply_tonal_regions_array

_KERNELS = r"""
#ifdef RGB16
typedef ushort channel_t;
#define RGB_MAX 65535
#define RGB_UNIT 257
#define ROUND_RGB(v) convert_ushort3_sat_rte(v)
#else
typedef uchar channel_t;
#define RGB_MAX 255
#define RGB_UNIT 1
#define ROUND_RGB(v) convert_uchar3_sat_rte(v)
#endif
#define LUMA_DIV (256 * RGB_UNIT)
#define GUIDE(v) (((v) + LUMA_DIV/2) / LUMA_DIV)
float3 finish_color(float3 c, __global const float *p) {
    c = (c - 0.18f) * p[12] + 0.18f;
    float3 pos = clamp(c, 0.0f, 1.0f);
    float3 excess = fmax(c - 1.0f, 0.0f);
    c += p[13] * 0.3f * pos * pos + p[14] * 1.2f * pos * (1-pos) * (1-pos);
    if (p[13] < 0) {
        float amount = -p[13] * 0.3f;
        float3 shoulder = 1.0f - amount*amount / (amount + (1.0f-2.0f*amount)*excess);
        c = select(c, shoulder, excess > 0);
    }
    if (p[16] > 0) {
        float l = dot(c, (float3)(0.2126f,0.7152f,0.0722f));
        c = l + (c-l) * p[15];
    }
    c = pow(clamp(c, 0.0f, 1.0f), (float3)(1.0f/2.2f));
    if (p[16] == 0) {
        float l = dot(c, (float3)(54.0f,183.0f,19.0f)) / 256.0f;
        c = clamp(l + (c-l)*p[15], 0.0f, 1.0f);
    }
    return c;
}
float3 calibrated_color(float3 c, __global const float *p) {
    if (p[17] >= 0) c = fmin(c, (float3)(p[17]));
    return finish_color((float3)(dot(c,vload3(0,p)),dot(c,vload3(1,p)),dot(c,vload3(2,p))),p);
}
float3 color(float3 c, __global const float *p) {
    return calibrated_color(c * (float3)(p[9],p[10],p[11]), p);
}
__kernel void tone(__global const float *src, __global channel_t *dst, __global const float *p) {
    int i = get_global_id(0);
    vstore3(ROUND_RGB(color(vload3(i,src),p)*(float)RGB_MAX),i,dst);
}
__kernel void bayer(__global const ushort *src, __global channel_t *dst,
    __global const float *p, __global const int *cfa, __global const float *black,
    int sw, int left, int top, int w, int h, float white) {
    int i = get_global_id(0), y=i/w, x=i%w;
    float3 rgb = (float3)(0);
    for(int ch=0;ch<3;ch++) {
        float sum=0, weights=0;
        for(int dy=-1;dy<=1;dy++) for(int dx=-1;dx<=1;dx++) {
            int sy=y+dy, sx=x+dx;
            if(sy<0 || sy>=h || sx<0 || sx>=w) continue;
            int pos=((sy+top)&1)*2+((sx+left)&1);
            if(cfa[pos]!=ch) continue;
            float weight;
            if(ch==1) {
                if(dx!=0 && dy!=0) continue;
                weight=(dx==0 && dy==0)?4.0f:1.0f;
            } else weight=(dx==0?2.0f:1.0f)*(dy==0?2.0f:1.0f);
            float v=clamp(((float)src[(sy+top)*sw+sx+left]-black[pos])/(white-black[pos]),0.0f,1.0f);
            sum+=v*weight; weights+=weight;
        }
        rgb[ch]=weights>0?sum/weights:0;
    }
    vstore3(ROUND_RGB(color(rgb,p)*(float)RGB_MAX),i,dst);
}
float mhc_sample(__global const ushort *src, __global const float *p,
    __global const int *cfa, __global const float *black,
    int sw, int left, int top, int w, int h, float white, int y, int x) {
    y=abs(y); x=abs(x);
    if(y>=h) y=abs(2*h-2-y);
    if(x>=w) x=abs(2*w-2-x);
    int pos=((y+top)&1)*2+((x+left)&1);
    return clamp(((float)src[(y+top)*sw+x+left]-black[pos])/(white-black[pos]),0.0f,1.0f)*p[9+cfa[pos]];
}
#define MHC(dy,dx) mhc_sample(src,p,cfa,black,sw,left,top,w,h,white,y+(dy),x+(dx))
__kernel void malvar(__global const ushort *src, __global channel_t *dst,
    __global const float *p, __global const int *cfa, __global const float *black,
    int sw, int left, int top, int w, int h, float white) {
    int i=get_global_id(0), y=i/w, x=i%w;
    float c=MHC(0,0), horizontal=MHC(0,-1)+MHC(0,1), vertical=MHC(-1,0)+MHC(1,0);
    float far_x=MHC(0,-2)+MHC(0,2), far_y=MHC(-2,0)+MHC(2,0);
    float diagonal=((MHC(-1,-1)+MHC(-1,1))+MHC(1,-1))+MHC(1,1);
    int pos=((y+top)&1)*2+((x+left)&1), ch=cfa[pos];
    float3 rgb;
    if(ch==1) {
        float along_x=(5.0f*c+4.0f*horizontal-diagonal-far_x+0.5f*far_y)/8.0f;
        float along_y=(5.0f*c+4.0f*vertical-diagonal-far_y+0.5f*far_x)/8.0f;
        rgb=cfa[pos^1]==0 ? (float3)(along_x,c,along_y) : (float3)(along_y,c,along_x);
    } else {
        float g=(4.0f*c+2.0f*(horizontal+vertical)-(far_x+far_y))/8.0f;
        float opposite=(6.0f*c+2.0f*diagonal-1.5f*(far_x+far_y))/8.0f;
        rgb=ch==0 ? (float3)(c,g,opposite) : (float3)(opposite,g,c);
    }
    vstore3(ROUND_RGB(calibrated_color(rgb,p)*(float)RGB_MAX),i,dst);
}
#undef MHC
__kernel void luminance_noise(__global const channel_t *src, __global channel_t *dst,
    __global const int *weights, int w, int h, int amount) {
    int i=get_global_id(0), y=i/w, x=i%w;
    int3 rgb=convert_int3(vload3(i,src));
    int weighted=54*rgb.x+183*rgb.y+19*rgb.z, guide=GUIDE(weighted);
    long sy=0, sw=0;
    for(int dy=-2;dy<=2;dy++) for(int dx=-2;dx<=2;dx++) {
        int yy=clamp(y+dy,0,h-1), xx=clamp(x+dx,0,w-1);
        int3 n=convert_int3(vload3(yy*w+xx,src));
        int nw=54*n.x+183*n.y+19*n.z;
        long weight=weights[((dy+2)*5+dx+2)*256+abs(GUIDE(nw)-guide)];
        sw+=weight; sy+=weight*(long)(nw-weighted);
    }
    long numerator=sy*(long)amount, denominator=sw*(long)(256*65536);
    long magnitude=numerator<0?-numerator:numerator;
    long whole=magnitude/denominator, remainder=magnitude%denominator;
    if(2*remainder>denominator || (2*remainder==denominator && whole%2==1)) whole++;
    int delta=(int)(numerator<0?-whole:whole);
    delta=clamp(delta,-min(rgb.x,min(rgb.y,rgb.z)),RGB_MAX-max(rgb.x,max(rgb.y,rgb.z)));
    vstore3(ROUND_RGB(convert_float3(rgb+delta)),i,dst);
}
__kernel void chroma_noise(__global const channel_t *src, __global channel_t *dst,
    __global const float *spatial, __global const float *light, __global const float *color_range,
    int w, int h, float strength) {
    int i=get_global_id(0), y=i/w, x=i%w;
    int3 rgb=convert_int3(vload3(i,src));
    int weighted=54*rgb.x+183*rgb.y+19*rgb.z, guide=GUIDE(weighted);
    float luma=(float)weighted/256.0f;
    int u=rgb.x-rgb.y, v=rgb.z-rgb.y;
    float su=0, sv=0, sw=0;
    for(int dy=-2;dy<=2;dy++) {
        int yy=clamp(y+dy,0,h-1);
        for(int dx=-2;dx<=2;dx++) {
            int xx=clamp(x+dx,0,w-1);
            int3 n=convert_int3(vload3(yy*w+xx,src));
            int nl=GUIDE(54*n.x+183*n.y+19*n.z), nu=n.x-n.y, nv=n.z-n.y;
            int difference=(abs(nu-u)+abs(nv-v)+RGB_UNIT/2)/RGB_UNIT;
            float weight=spatial[(dy+2)*5+dx+2]*light[abs(nl-guide)]*color_range[difference];
            sw+=weight; su+=weight*(float)nu; sv+=weight*(float)nv;
        }
    }
    float fu=(float)u+strength*(su/sw-(float)u), fv=(float)v+strength*(sv/sw-(float)v);
    float offset=(54.0f*fu+19.0f*fv)/256.0f;
    float3 c=(float3)(fu-offset,-offset,fv-offset);
    float scale=1.0f;
    for(int ch=0;ch<3;ch++) {
        if(c[ch]>0) scale=fmin(scale,((float)RGB_MAX-luma)/c[ch]);
        else if(c[ch]<0) scale=fmin(scale,-luma/c[ch]);
    }
    vstore3(ROUND_RGB((float3)(luma)+scale*c),i,dst);
}
"""


def color_parameters(
    matrix,
    gains,
    *,
    contrast=0.0,
    highlights=0.0,
    shadows=0.0,
    saturation=0.0,
    linear_saturation=False,
    highlight_ceiling=None,
):
    return np.asarray(
        [
            *np.asarray(matrix, dtype=np.float32).ravel(),
            *gains,
            1 + np.clip(contrast, -1, 1) * 0.75,
            np.clip(highlights, -1, 1),
            np.clip(shadows, -1, 1),
            1 + np.clip(saturation, -1, 1) * 0.75,
            int(linear_saturation),
            -1 if highlight_ceiling is None else highlight_ceiling,
        ],
        dtype=np.float32,
    )


def tone_cpu(pixels, params, *, bit_depth=8):
    if bit_depth not in (8, 16):
        raise ValueError("Tone output bit depth must be 8 or 16")
    camera = pixels * params[9:12]
    if params[17] >= 0:
        np.minimum(camera, params[17], out=camera)
    rgb = camera @ params[:9].reshape(3, 3).T
    rgb = (rgb - 0.18) * params[12] + 0.18
    apply_tonal_regions_array(rgb, highlights=params[13], shadows=params[14])
    if params[16]:
        luma = (rgb * np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)).sum(
            axis=-1, keepdims=True
        )
        rgb = luma + (rgb - luma) * params[15]
    rgb = np.clip(rgb, 0, 1) ** (1 / 2.2)
    if not params[16]:
        luma = (rgb * np.array([54, 183, 19], dtype=np.float32)).sum(
            axis=-1, keepdims=True
        ) / 256
        rgb = luma + (rgb - luma) * params[15]
    return np.rint(np.clip(rgb, 0, 1) * ((1 << bit_depth) - 1)).astype(np.uint8 if bit_depth == 8 else np.uint16)


class OpenClRenderer:
    def __init__(self, cl, device):
        self.cl = cl
        self.name = device.name.strip()
        self.context = cl.Context([device])
        self.queue = cl.CommandQueue(self.context)
        self.program = cl.Program(self.context, _KERNELS).build()
        self.tone_kernel = cl.Kernel(self.program, "tone")
        self.bayer_kernel = cl.Kernel(self.program, "bayer")
        self.malvar_kernel = cl.Kernel(self.program, "malvar")
        self._malvar_validated = False
        self.chroma_kernel = cl.Kernel(self.program, "chroma_noise")
        self._chroma_validated = False
        self.luminance_kernel = cl.Kernel(self.program, "luminance_noise")
        self._luminance_validated = False
        self._cached_pixels = None
        self._pixel_buffer = None
        self._kernels16 = None
        self._validated16 = set()

    def _kernel(self, name, bit_depth):
        if bit_depth == 8:
            return getattr(self, name + "_kernel")
        if bit_depth != 16:
            raise ValueError("GPU output bit depth must be 8 or 16")
        if self._kernels16 is None:
            program = self.cl.Program(self.context, _KERNELS).build(options=["-DRGB16"])
            self._kernels16 = {
                key: self.cl.Kernel(program, key + ("_noise" if key in ("chroma", "luminance") else ""))
                for key in ("tone", "bayer", "malvar", "chroma", "luminance")
            }
        return self._kernels16[name]

    def buffer(self, data):
        return self.cl.Buffer(
            self.context,
            self.cl.mem_flags.READ_ONLY | self.cl.mem_flags.COPY_HOST_PTR,
            hostbuf=np.ascontiguousarray(data),
        )

    def tone(self, pixels, params, *, bit_depth=8):
        if self._cached_pixels is not pixels:
            self._pixel_buffer = self.buffer(pixels)
            self._cached_pixels = pixels
        output = np.empty(pixels.shape, dtype=np.uint8 if bit_depth == 8 else np.uint16)
        destination = self.cl.Buffer(
            self.context, self.cl.mem_flags.WRITE_ONLY, output.nbytes
        )
        self._kernel("tone", bit_depth)(
            self.queue,
            (pixels.size // 3,),
            None,
            self._pixel_buffer,
            destination,
            self.buffer(params),
        )
        self.cl.enqueue_copy(self.queue, output, destination).wait()
        return output

    def luminance(self, pixels, strength):
        bit_depth = pixels.dtype.itemsize * 8
        if not (self._luminance_validated if bit_depth == 8 else "luminance" in self._validated16):
            from openraw_studio.raw.native.luminance import _reference_chunk

            sample = np.random.default_rng(217).integers(0, 1 << bit_depth, (9, 11, 3), dtype=pixels.dtype)
            expected = _reference_chunk(sample, 0, 9, .73)
            actual = self._luminance(sample, .73)
            if not np.array_equal(actual, expected):
                raise RuntimeError("GPU luminance-noise validation failed")
            if bit_depth == 8:
                self._luminance_validated = True
            else:
                self._validated16.add("luminance")
        return self._luminance(pixels, strength)

    def _luminance(self, pixels, strength):
        from openraw_studio.raw.native.luminance import WEIGHTS

        height, width, _ = pixels.shape
        output = np.empty_like(pixels)
        destination = self.cl.Buffer(self.context, self.cl.mem_flags.WRITE_ONLY, output.nbytes)
        self._kernel("luminance", pixels.dtype.itemsize * 8)(
            self.queue, (height * width,), None, self.buffer(pixels), destination,
            self.buffer(WEIGHTS), np.int32(width), np.int32(height), np.int32(round(strength * 65536)),
        )
        self.cl.enqueue_copy(self.queue, output, destination).wait()
        return output

    def chroma(self, pixels, strength):
        bit_depth = pixels.dtype.itemsize * 8
        if not (self._chroma_validated if bit_depth == 8 else "chroma" in self._validated16):
            from openraw_studio.raw.native.chroma import _reference_chunk

            sample = np.random.default_rng(215).integers(0, 1 << bit_depth, (9, 11, 3), dtype=pixels.dtype)
            expected = _reference_chunk(sample, 0, 9, .73)
            actual = self._chroma(sample, .73)
            if actual.shape != expected.shape or np.max(np.abs(actual.astype(int) - expected.astype(int))) > 1:
                raise RuntimeError("GPU color-noise validation failed")
            if bit_depth == 8:
                self._chroma_validated = True
            else:
                self._validated16.add("chroma")
        return self._chroma(pixels, strength)

    def _chroma(self, pixels, strength):
        from openraw_studio.raw.native.chroma import COLOR, LIGHT, SPATIAL

        height, width, _ = pixels.shape
        output = np.empty_like(pixels)
        destination = self.cl.Buffer(self.context, self.cl.mem_flags.WRITE_ONLY, output.nbytes)
        self._kernel("chroma", pixels.dtype.itemsize * 8)(
            self.queue, (height * width,), None, self.buffer(pixels), destination,
            self.buffer(SPATIAL), self.buffer(LIGHT), self.buffer(COLOR),
            np.int32(width), np.int32(height), np.float32(strength),
        )
        self.cl.enqueue_copy(self.queue, output, destination).wait()
        return output

    def bayer(self, raw_bytes, source_width, crop, pattern, black, white, params, *, method="bilinear", bit_depth=8):
        if method == "malvar" and not (self._malvar_validated if bit_depth == 8 else "malvar" in self._validated16):
            if not _validate_malvar_renderer(self, bit_depth=bit_depth):
                raise RuntimeError("GPU MHC validation failed")
            if bit_depth == 8:
                self._malvar_validated = True
            else:
                self._validated16.add("malvar")
        return self._bayer(raw_bytes, source_width, crop, pattern, black, white, params, method=method, bit_depth=bit_depth)

    def _bayer(self, raw_bytes, source_width, crop, pattern, black, white, params, *, method="bilinear", bit_depth=8):
        left, top, width, height = crop
        output = np.empty((height, width, 3), dtype=np.uint8 if bit_depth == 8 else np.uint16)
        destination = self.cl.Buffer(
            self.context, self.cl.mem_flags.WRITE_ONLY, output.nbytes
        )
        kernel = self._kernel("malvar" if method == "malvar" else "bayer", bit_depth)
        kernel(
            self.queue,
            (width * height,),
            None,
            self.buffer(np.frombuffer(raw_bytes, dtype="<u2")),
            destination,
            self.buffer(params),
            self.buffer(np.asarray(pattern, dtype=np.int32)),
            self.buffer(np.asarray(black, dtype=np.float32)),
            np.int32(source_width),
            np.int32(left),
            np.int32(top),
            np.int32(width),
            np.int32(height),
            np.float32(white),
        )
        self.cl.enqueue_copy(self.queue, output, destination).wait()
        return output


_local = threading.local()


def _validate_malvar_renderer(renderer, *, bit_depth=8):
    from openraw_studio.raw.native.malvar import demosaic_chunk

    sample = np.random.default_rng(74).integers(0, 17000, (8, 10), dtype=np.uint16)
    pattern, black, gains = (1, 2, 0, 1), (16, 32, 48, 64), (1.8, 1, 1.4)
    crop = (1, 1, 8, 6)
    params = color_parameters(((1.3,-.2,-.1),(-.1,1.2,-.1),(.1,-.2,1.1)), gains,
                              highlights=-.3, shadows=.4, highlight_ceiling=1)
    expected_camera = np.stack(demosaic_chunk(sample, crop, 0, 6, pattern, black, 16383, gains), axis=2)
    calibrated = params.copy()
    calibrated[9:12] = 1
    expected = tone_cpu(expected_camera, calibrated, bit_depth=bit_depth)
    actual = renderer._bayer(sample.tobytes(), 10, crop, pattern, black, 16383, params, method="malvar", bit_depth=bit_depth)
    return actual.shape == expected.shape and np.max(np.abs(actual.astype(int) - expected.astype(int))) <= (1 if bit_depth == 8 else 4)


def _validate_tone_renderer(renderer):
    sample = np.array([[[0.1, 0.5, 0.9], [0, 1, 0.3], [1.4, 2, 4]]], dtype=np.float32)
    for options in ({}, {"highlights": -1}, {"highlights": -.3, "linear_saturation": True}):
        params = color_parameters(np.eye(3), (1, 1, 1), **options)
        actual = renderer.tone(sample, params)
        if np.max(np.abs(actual.astype(int) - tone_cpu(sample, params).astype(int))) > 1:
            return False
    return True


def get_gpu():
    """Choose a working GPU per worker; never prompt or require GPU drivers."""
    if os.environ.get("OPENRAW_GPU", "auto").lower() in {"0", "off", "cpu"}:
        return None
    if hasattr(_local, "gpu"):
        return _local.gpu
    _local.gpu = None
    try:
        import pyopencl as cl

        devices = []
        for platform in cl.get_platforms():
            try:
                devices.extend(platform.get_devices(device_type=cl.device_type.GPU))
            except cl.Error:
                continue
        devices.sort(
            key=lambda d: (not d.host_unified_memory, d.max_compute_units), reverse=True
        )
        for device in devices:
            try:
                renderer = OpenClRenderer(cl, device)
                if _validate_tone_renderer(renderer):
                    _local.gpu = renderer
                    break
            except Exception:
                continue
    except Exception:
        pass
    return _local.gpu


def disable_gpu():
    _local.gpu = None


def render_tone(pixels, params):
    gpu = get_gpu()
    if gpu is not None:
        try:
            return gpu.tone(pixels, params), "GPU: " + gpu.name
        except Exception:
            disable_gpu()
    from openraw_studio.raw.native.compiled_tone import render

    compiled = render(pixels, params)
    if compiled is not None:
        return compiled, "CPU"
    return tone_cpu(pixels, params), "CPU"
