"""Exact UI opacity for the project's non-interlaced 8-bit RGBA PNG art.

Returns display bytes only; never rewrites source assets. No imaging package
is required by the desktop application.
"""
import struct
import zlib


def display_png(data, numerator=3, denominator=5):
    if not 0 <= numerator <= denominator or denominator <= 0:
        raise ValueError('Invalid opacity')
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Invalid PNG')
    chunks, offset = [], 8
    while offset < len(data):
        size = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset+4:offset+8]
        payload = data[offset+8:offset+8+size]
        if len(payload) != size or offset+12+size > len(data):
            raise ValueError('Truncated PNG')
        chunks.append((kind, payload))
        offset += size+12
    header = chunks[0][1]
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', header)
    if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
        raise ValueError('Opacity artwork must be 8-bit RGBA without interlacing')
    raw = zlib.decompress(b''.join(payload for kind,payload in chunks if kind == b'IDAT'))
    stride = width*4
    if len(raw) != (stride+1)*height:
        raise ValueError('Invalid PNG pixel length')
    alpha = bytes((a*numerator+denominator//2)//denominator for a in range(256))
    previous, output = bytearray(stride), bytearray()
    for y in range(height):
        start = y*(stride+1)
        mode, row = raw[start], bytearray(raw[start+1:start+1+stride])
        if mode not in range(5):
            raise ValueError('Invalid PNG filter')
        if mode:
            for i in range(stride):
                left = row[i-4] if i >= 4 else 0
                up, corner = previous[i], previous[i-4] if i >= 4 else 0
                if mode == 1:
                    predictor = left
                elif mode == 2:
                    predictor = up
                elif mode == 3:
                    predictor = (left+up)//2
                else:
                    p = left+up-corner
                    distances = abs(p-left), abs(p-up), abs(p-corner)
                    predictor = (left, up, corner)[distances.index(min(distances))]
                row[i] = (row[i]+predictor) & 255
        previous = row[:]
        row[3::4] = row[3::4].translate(alpha)
        output.append(0)
        output.extend(row)
    def chunk(kind, payload):
        return (struct.pack('>I',len(payload))+kind+payload+
                struct.pack('>I',zlib.crc32(kind+payload) & 0xffffffff))
    return (data[:8]+chunk(b'IHDR',header)+chunk(b'IDAT',zlib.compress(output))+
            chunk(b'IEND',b''))
