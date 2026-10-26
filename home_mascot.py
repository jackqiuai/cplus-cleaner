"""Resolution independent sweeping assistant, drawn by Tk without runtime assets."""
import math


def draw(canvas, cx, top, elapsed=0, state="idle", scale=1):
    canvas.delete("mascot")
    mint, dark, pale = "#3bd2a0", "#237b70", "#e2f8ef"
    moving = state == "scanning"
    wave = math.sin(elapsed * math.tau / 1.6) if moving else 0
    bob = 1.6 * wave

    def coords(values):
        return [cx + v*scale if i % 2 == 0 else top + v*scale for i, v in enumerate(values)]

    def oval(box, fill, **kw):
        return canvas.create_oval(*coords(box), fill=fill, outline="", tags="mascot", **kw)

    def line(points, color, width, **kw):
        return canvas.create_line(*coords(points), fill=color, width=width*scale,
                                  capstyle="round", joinstyle="round", tags="mascot", **kw)

    def box(bounds, radius, fill):
        x1, y1, x2, y2 = bounds
        points = (x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,
                  x2,y2,x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1)
        canvas.create_polygon(*coords(points), smooth=True, splinesteps=20,
                              fill=fill, outline="", tags="mascot")

    oval((-106, 13, 108, 174), "#edfaf4")
    oval((-85, 153, 92, 168), "#d0eee0")
    line((-81, 161, 98, 161), "#b5dfce", 1)
    # Antenna, ears and friendly face.
    line((-20, 31+bob, -20, 18+bob), dark, 4)
    oval((-25, 10+bob, -15, 20+bob), mint)
    box((-72, 52+bob, -60, 76+bob), 6, dark)
    box((22, 52+bob, 34, 76+bob), 6, dark)
    box((-65, 31+bob, 27, 89+bob), 20, mint)
    box((-57, 39+bob, 19, 80+bob), 14, "#ffffff")
    oval((-41, 51+bob, -32, 64+bob), dark)
    oval((-6, 51+bob, 3, 64+bob), dark)
    line((-28, 68+bob, -20, 72+bob, -12, 68+bob), dark, 2, smooth=True)
    line((-20, 88+bob, -20, 95+bob), dark, 8)
    box((-48, 93+bob, 8, 132+bob), 12, mint)
    box((-33, 102+bob, -6, 121+bob), 6, pale)
    # Little power light and stable feet keep the motion calm.
    oval((-22, 108+bob, -16, 114+bob), mint)
    line((-36, 131+bob, -39, 147), dark, 8)
    line((-6, 131+bob, -1, 147), dark, 8)
    box((-54, 144, -25, 156), 6, dark)
    box((-13, 144, 17, 156), 6, dark)
    # Broom pivots with both hands following its handle.
    grip = (23, 102+bob)
    tip = (63 + 18*wave, 145)
    start = (grip[0] - (tip[0]-grip[0])*.75, grip[1] - (tip[1]-grip[1])*.75)
    hand2 = (grip[0]+(tip[0]-grip[0])*.36, grip[1]+(tip[1]-grip[1])*.36)
    line((-48, 101+bob, -52, 118+bob, *grip), dark, 9)
    line((7, 101+bob, 14, 119+bob, *hand2), dark, 9)
    line((*start, *tip), "#398b77", 5)
    oval((grip[0]-6, grip[1]-6, grip[0]+6, grip[1]+6), mint)
    oval((hand2[0]-6, hand2[1]-6, hand2[0]+6, hand2[1]+6), mint)
    tx, ty = tip
    canvas.create_polygon(*coords((tx-10,ty-4,tx+8,ty-7,tx+23,ty+12,tx-13,ty+15)),
                          fill="#efc773", outline="", tags="mascot")
    line((tx-10,ty-3,tx+8,ty-6), dark, 4)
    for offset in (-5, 2, 9):
        line((tx+offset,ty+2,tx+offset+4,ty+11), "#d0a95d", 1.3)
    if moving:
        # Decorative paper is animated only while scanning; no fabricated progress.
        for index in range(3):
            phase = (elapsed/1.6 + index/3) % 1
            x, y = tx+15+26*phase, 154-8*math.sin(phase*math.pi)
            shade = ("#76cfac", "#a0ddc4", "#cceee0")[min(2, int(phase*3))]
            canvas.create_oval(*coords((x,y,x+3,y+3)), fill=shade, outline="",
                               tags=("mascot", "dust"))
        line((tx+22, 139, tx+29, 142, tx+33, 146), "#93d6bb", 2, smooth=True)
    elif state == "done":
        oval((52, 23, 82, 53), mint)
        line((60, 38, 65, 43, 75, 32), "#ffffff", 3)
    else:
        line((68, 39, 68, 51), "#91d5b8", 2)
        line((62, 45, 74, 45), "#91d5b8", 2)
