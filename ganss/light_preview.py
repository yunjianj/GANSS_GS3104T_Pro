"""Illustrative lighting patterns, not a simulation of device firmware."""
import colorsys
import math


def preview_color(mode, rgb, brightness, speed, colorful, x, y, index, elapsed, events=()):
    if mode == 0 or brightness <= 0:
        return (0, 0, 0)
    t = elapsed * (0.25 + max(0, min(speed, 5)) * 0.28)
    nx, ny = x / 800.0, y / 280.0
    phase = (index * 0.61803398875) % 1
    wave = lambda v: (math.sin(v * math.tau) + 1) / 2
    if mode == 1:
        level = 1.0
    elif mode in (2, 3, 13, 14, 15):
        pulses = []
        for source, sx, sy, age in events:
            if age < 0:
                continue
            progress = age * (0.5 + max(0, min(speed, 5)) * .3)
            if progress >= 1:
                continue
            if mode in (2, 3):
                strength = (1 - progress) if source == index else 0
            else:
                distance = math.hypot((x - sx) / 800, (y - sy) / 280)
                radius = progress * (1.4 if mode == 14 else 1.0)
                strength = max(0, 1 - abs(distance - radius) * 6) * (1 - progress)
            pulses.append(strength)
        pulse = max(pulses, default=0)
        level = 1 - pulse if mode == 3 else pulse
    elif mode == 4:
        level = wave(t + phase * 13) ** 8
    elif mode == 5:
        level = wave(t - ny * 1.8 + phase * 3) ** 5
    elif mode == 6:
        level = 0.5 + 0.5 * wave(t * .5 + phase)
    elif mode == 7:
        level = 0.15 + 0.85 * wave(t * .55)
    elif mode == 8:
        level = 1.0
    elif mode == 9:
        level = wave(t - abs(nx - .5) * 3) ** 2
    elif mode in (10, 11, 16):
        level = 0.2 + 0.8 * wave(t - nx * (2 if mode == 11 else 3))
    elif mode == 12:
        level = 0.2 + 0.8 * wave(t + math.atan2(ny - .5, nx - .5) / math.tau)
    elif mode == 17:
        level = wave(t - nx * 4) ** 3
    elif mode == 18:
        level = wave(t - nx * 2 - ny) ** 3
    else:
        level = wave((nx - (0.5 + 0.5 * math.sin(t * 2))) * 2) ** 6
    if colorful or mode in (6, 8):
        hue = (nx * .7 + (0 if mode in (2, 3, 13, 14, 15) else t * .12) + (phase * .3 if mode == 6 else 0)) % 1
        base = colorsys.hsv_to_rgb(hue, 1, 1)
    else:
        base = rgb
    scale = max(0, min(brightness, 5)) / 5 * level
    return tuple(max(0, min(255, round(c * scale * 255))) for c in base)
