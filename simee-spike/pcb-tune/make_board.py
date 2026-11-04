"""A small two-layer board for the pcb tune-test spike: SIG_A straight (40 mm), SIG_B with a bend
(longer), and OBST, a net running 1.2 mm beside SIG_A so the meanders must keep clear of it."""
import sys, uuid

def u():
    return str(uuid.uuid4())

nets = ["", "SIG_A", "SIG_B", "OBST", "USB_P", "USB_N"]
pads = [  # ref, x, y, net
    ("TP1", 5, 10, 1), ("TP2", 45, 10, 1),
    ("TP3", 5, 20, 2), ("TP4", 45, 25, 2),
    ("TP5", 5, 8.8, 3), ("TP6", 45, 8.8, 3),
    ("TP7", 5, 32, 4), ("TP8", 45, 32, 4),         # a differential pair, 0.2 mm tracks 0.4 mm apart:
    ("TP9", 5, 32.6, 5), ("TP10", 45, 33.6, 5),    # USB_N bends at its end, 0.41 mm longer
]
tracks = [  # x1 y1 x2 y2 net
    (5, 10, 45, 10, 1),
    (5, 20, 25, 20, 2), (25, 20, 30, 25, 2), (30, 25, 45, 25, 2),
    (5, 8.8, 45, 8.8, 3),
    (5, 32, 45, 32, 4),
    (5, 32.6, 43, 32.6, 5), (43, 32.6, 44, 33.6, 5), (44, 33.6, 45, 33.6, 5),
]
out = ["(kicad_pcb (version 20240108) (generator \"pcbnew\") (generator_version \"8.0\")",
       " (general (thickness 1.6) (legacy_teardrops no)) (paper \"A4\")",
       " (layers (0 \"F.Cu\" signal) (31 \"B.Cu\" signal) (36 \"B.SilkS\" user \"B.Silkscreen\")"
       " (37 \"F.SilkS\" user \"F.Silkscreen\") (38 \"B.Mask\" user) (39 \"F.Mask\" user)"
       " (35 \"F.Paste\" user) (44 \"Edge.Cuts\" user) (47 \"F.CrtYd\" user \"F.Courtyard\") (49 \"F.Fab\" user))",
       " (setup (pad_to_mask_clearance 0))"]
out += [f" (net {i} \"{n}\")" for i, n in enumerate(nets)]
for ref, x, y, net in pads:
    out.append(f" (footprint \"simee:TP\" (layer \"F.Cu\") (uuid \"{u()}\") (at {x} {y})"
               f" (property \"Reference\" \"{ref}\" (at 0 0 0) (layer \"F.Fab\") (uuid \"{u()}\")"
               f" (effects (font (size 0.5 0.5) (thickness 0.1))))"
               f" (property \"Value\" \"TP\" (at 0 0 0) (layer \"F.Fab\") (hide yes) (uuid \"{u()}\")"
               f" (effects (font (size 0.5 0.5) (thickness 0.1))))"
               f" (attr smd)"
               f" (pad \"1\" smd rect (at 0 0) (size {0.4 if net >= 4 else 0.8} {0.4 if net >= 4 else 0.8}) (layers \"F.Cu\" \"F.Mask\")"
               f" (net {net} \"{nets[net]}\") (uuid \"{u()}\")))")
for x1, y1, x2, y2, net in tracks:
    out.append(f" (segment (start {x1} {y1}) (end {x2} {y2}) (width {0.2 if net >= 4 else 0.25}) (layer \"F.Cu\") (net {net}) (uuid \"{u()}\"))")
out.append(f" (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1) (type default)) (fill none)"
           f" (layer \"Edge.Cuts\") (uuid \"{u()}\"))")
out.append(")")
open(sys.argv[1], "w").write("\n".join(out) + "\n")
