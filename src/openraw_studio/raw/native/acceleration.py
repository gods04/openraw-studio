"""Optional, validated OpenCL rendering with a NumPy fallback."""

from __future__ import annotations

import os
import threading

import numpy as np

from openraw_studio.raw.native.tonal import apply_tonal_regions_array

_KERNELS = r"""
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
float3 color(float3 c, __global const float *p) {
    c *= (float3)(p[9],p[10],p[11]);
    if (p[17] >= 0) c = fmin(c, (float3)(p[17]));
    return finish_color((float3)(dot(c,vload3(0,p)),dot(c,vload3(1,p)),dot(c,vload3(2,p))),p);
}
__kernel void tone(__global const float *src, __global uchar *dst, __global const float *p) {
    int i = get_global_id(0);
    vstore3(convert_uchar3_sat_rte(color(vload3(i,src),p)*255.0f),i,dst);
}
__kernel void bayer(__global const ushort *src, __global uchar *dst,
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
    vstore3(convert_uchar3_sat_rte(color(rgb,p)*255.0f),i,dst);
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


def tone_cpu(pixels, params):
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
    return np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8)


class OpenClRenderer:
    def __init__(self, cl, device):
        self.cl = cl
        self.name = device.name.strip()
        self.context = cl.Context([device])
        self.queue = cl.CommandQueue(self.context)
        self.program = cl.Program(self.context, _KERNELS).build()
        self.tone_kernel = cl.Kernel(self.program, "tone")
        self.bayer_kernel = cl.Kernel(self.program, "bayer")
        self._cached_pixels = None
        self._pixel_buffer = None

    def buffer(self, data):
        return self.cl.Buffer(
            self.context,
            self.cl.mem_flags.READ_ONLY | self.cl.mem_flags.COPY_HOST_PTR,
            hostbuf=np.ascontiguousarray(data),
        )

    def tone(self, pixels, params):
        if self._cached_pixels is not pixels:
            self._pixel_buffer = self.buffer(pixels)
            self._cached_pixels = pixels
        output = np.empty(pixels.shape, dtype=np.uint8)
        destination = self.cl.Buffer(
            self.context, self.cl.mem_flags.WRITE_ONLY, output.nbytes
        )
        self.tone_kernel(
            self.queue,
            (pixels.size // 3,),
            None,
            self._pixel_buffer,
            destination,
            self.buffer(params),
        )
        self.cl.enqueue_copy(self.queue, output, destination).wait()
        return output

    def bayer(self, raw_bytes, source_width, crop, pattern, black, white, params):
        left, top, width, height = crop
        output = np.empty((height, width, 3), dtype=np.uint8)
        destination = self.cl.Buffer(
            self.context, self.cl.mem_flags.WRITE_ONLY, output.nbytes
        )
        self.bayer_kernel(
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
    return tone_cpu(pixels, params), "CPU"
