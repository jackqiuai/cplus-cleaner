"""Stationary mint computer with animated scanning orbit, drawn by Tk."""
import math


def draw(canvas, cx, top, elapsed=0, state="idle", scale=1):
    canvas.delete("core-art")
    mint = "#3bd2a0"
    scanning = state == "scanning"

    def xy(values):
        return [cx+v*scale if i%2 == 0 else top+v*scale for i,v in enumerate(values)]

    def oval(bounds, fill, outline="", width=1, tags=()):
        return canvas.create_oval(*xy(bounds),fill=fill,outline=outline,width=width*scale,
                                  tags=("core-art",*tags))

    def line(points, color, width, tags=()):
        return canvas.create_line(*xy(points),fill=color,width=width*scale,capstyle="round",
                                  joinstyle="round",tags=("core-art",*tags))

    def box(bounds, radius, fill, outline="", tags=()):
        x1,y1,x2,y2 = bounds
        points = (x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,
                  x2,y2,x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1)
        canvas.create_polygon(*xy(points),smooth=True,splinesteps=24,fill=fill,
                              outline=outline,width=scale,tags=("core-art",*tags))

    oval((-113,4,113,175),"#f0faf6")
    oval((-95,24,95,154),"","#d9eee5")
    oval((-108,11,108,167),"","#dfefe8")
    oval((-57,147,57,156),"#d9ebe3")
    if scanning:
        angle = (elapsed/4.8*360)%360
        canvas.create_arc(*xy((-108,11,108,167)),start=angle,extent=76,style="arc",
                          outline=mint,width=2.8*scale,tags=("core-art","scan-orbit"))
        radians = math.radians(angle+76)
        x,y = 108*math.cos(radians),89-78*math.sin(radians)
        oval((x-3,y-3,x+3,y+3),mint,tags=("scan-orbit",))
    # Identical geometry and colors in every state: the computer never moves.
    computer = ("core-computer",)
    box((-57,45,57,128),9,"#9edfc0",tags=computer)
    box((-50,52,50,112),4,"#f4fcf7",tags=computer)
    canvas.create_polygon(*xy((-47,55,7,55,-27,109,-47,109)),
                          fill="#e5f6ec",outline="",tags=("core-art",*computer))
    oval((-2,118,2,122),"#68b895",tags=computer)
    canvas.create_polygon(*xy((-8,128,8,128,13,141,-13,141)),
                          fill="#88cfae",outline="",tags=("core-art",*computer))
    box((-29,140,29,147),3,"#9edfc0",tags=computer)

    initial = ((-87,40),(83,39),(-85,130),(86,128))
    for index,(x,y) in enumerate(initial):
        if scanning:
            # A calm sorting motion around the core; documents remain visible.
            phase = elapsed*math.tau/4.8+index*math.pi/2
            x += 5*math.sin(phase)
            y += 5*math.cos(phase)
        elif state == "done":
            x,y = -66+44*index,157
        box((x-13,y-16,x+13,y+16),5,"#ffffff","#cde6db")
        if index == 1:
            box((x-8,y-8,x+1,y-3),2,"#c3ecdc")
            box((x-8,y-5,x+8,y+7),2,"#8ddcbe")
        elif index == 2:
            box((x-8,y-9,x+8,y+7),3,"#e2f4ec")
            line((x-4,y-4,x+4,y-4),"#86cdb1",1.5)
            line((x-4,y+1,x+2,y+1),"#86cdb1",1.5)
        else:
            line((x-7,y-6,x+6,y-6),"#94cdb8",1.5)
            line((x-7,y,x+6,y),"#badfcd",1.5)
            line((x-7,y+6,x+2,y+6),"#badfcd",1.5)
        if scanning:
            # A stable semantic tag lets previews verify real frame motion.
            oval((x+8,y+10,x+11,y+13),mint,tags=("scan-orbit",))
    if state == "done":
        oval((62,12,88,38),mint,tags=("core-complete",))
        line((69,25,74,30,82,20),"#ffffff",2.6)
    elif state == "idle":
        oval((63,16,67,20),"#b5dfcd")
        oval((-65,155,-62,158),"#b5dfcd")

# Purpose: Home feature catalog, disk data and totals
