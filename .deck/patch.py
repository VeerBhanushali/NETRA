import pathlib

p = pathlib.Path("build.js")
s = p.read_text(encoding="utf-8")
applied, failed = [], []


def sub(old, new, label):
    global s
    if old in s:
        s = s.replace(old, new)
        applied.append(label)
    else:
        failed.append(label)


# --- a coloured dot instead of an edge stripe -------------------------
sub(
    "function stat(s, x, y, w, value, label, color) {",
    """// A small filled square before a category label. Replaces the coloured
// edge stripe earlier versions drew across the top of each card: an edge
// stripe reads as decoration, a dot beside the word it colours reads as
// a key.
function tag(s, x, y, w, text, color, size = 12.5) {
  s.addShape(pres.ShapeType.rect,
    { x, y: y + 0.055, w: 0.13, h: 0.13, fill: { color } });
  s.addText(text, {
    x: x + 0.25, y, w: w - 0.25, h: 0.3, isTextBox: true, margin: 0,
    valign: "top", fontFace: F, fontSize: size, bold: true,
    charSpacing: 1.2, color,
  });
}
function stat(s, x, y, w, value, label, color) {""",
    "tag() helper")

sub('"Indian plate grammar fixes O<->0, I<->1, B<->8 by position."',
    '"Indian plate grammar fixes O/0, I/1 and B/8 by position."',
    "slide3 confusion pairs")

# --- slide 7 architecture ---------------------------------------------
sub("""    card(s, x, 2.1, cw, 2.5);
    s.addShape(pres.ShapeType.rect,
      { x, y: 2.1, w: cw, h: 0.09, fill: { color: c } });
    s.addText(t, { x: x + 0.18, y: 2.42, w: cw - 0.36, h: 0.32, isTextBox: true,
      margin: 0, fontFace: F, fontSize: 12.5, bold: true, charSpacing: 1, color: c });
    s.addText(d, { x: x + 0.18, y: 2.82, w: cw - 0.36, h: 1.6, isTextBox: true,
      margin: 0, fontFace: F, fontSize: 12, color: INK_BODY, lineSpacingMultiple: 1.3 });""",
    """    card(s, x, 2.1, cw, 2.35);
    tag(s, x + 0.2, 2.32, cw - 0.4, t, c, 12);
    s.addText(d, { x: x + 0.2, y: 2.76, w: cw - 0.4, h: 1.45, isTextBox: true,
      margin: 0, valign: "top", fontFace: F, fontSize: 12, color: INK_BODY,
      lineSpacingMultiple: 1.3 });""",
    "slide7 cards")

sub("""    s.addText(t, { x: x2, y: 4.95, w: nw, h: 0.32, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 13, bold: true, color: BLUE });
    s.addText(d, { x: x2, y: 5.3, w: nw, h: 1.0, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 11.5, color: INK_BODY, lineSpacingMultiple: 1.2 });""",
    """    s.addText(t, { x: x2, y: 4.72, w: nw, h: 0.32, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 13, bold: true, color: BLUE });
    s.addText(d, { x: x2, y: 5.07, w: nw, h: 1.1, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 11.5, color: INK_BODY,
      lineSpacingMultiple: 1.2 });""",
    "slide7 notes")

# --- slide 8 tech stack ------------------------------------------------
sub("""    card(s, x, 1.95, cw, 3.9);
    s.addShape(pres.ShapeType.rect,
      { x, y: 1.95, w: cw, h: 0.09, fill: { color: c } });
    s.addText(t, { x: x + 0.28, y: 2.26, w: cw - 0.56, h: 0.34, isTextBox: true,
      margin: 0, fontFace: F, fontSize: 12.5, bold: true, charSpacing: 1.2, color: c });
    s.addText(items.map((it, i) => ({
      text: it, options: { bullet: true, breakLine: i < items.length - 1 },
    })), { x: x + 0.28, y: 2.72, w: cw - 0.5, h: 2.9, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 12.5, color: INK_BODY, paraSpaceAfter: 7 });""",
    """    card(s, x, 1.95, cw, 3.4);
    tag(s, x + 0.28, 2.18, cw - 0.56, t, c);
    s.addText(items.map((it, i) => ({
      text: it, options: { bullet: true, breakLine: i < items.length - 1 },
    })), { x: x + 0.28, y: 2.6, w: cw - 0.5, h: 2.6, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 12.5, color: INK_BODY,
      paraSpaceAfter: 7 });""",
    "slide8 cards")

sub("""    { x: M, y: 6.05, w: 11.9, h: 0.5, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 12, italic: true, color: INK_MUTE });""",
    """    { x: M, y: 5.62, w: 11.9, h: 0.5, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 12, italic: true, color: INK_MUTE });""",
    "slide8 footnote")

# --- slide 11 status ---------------------------------------------------
sub("""  card(s, M, 1.95, 7.6, 4.3);
  s.addText("WORKING TODAY", { x: M + 0.3, y: 2.16, w: 5, h: 0.32,
    isTextBox: true, margin: 0, fontFace: F, fontSize: 12.5, bold: true,
    charSpacing: 1.2, color: GREEN });""",
    """  card(s, M, 1.95, 7.6, 3.55);
  tag(s, M + 0.3, 2.16, 5, "WORKING TODAY", GREEN);""",
    "slide11 left card")

sub("""    { x: M + 0.3, y: 2.6, w: 7.0, h: 3.4, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 12.5, color: INK_BODY, paraSpaceAfter: 7 });""",
    """    { x: M + 0.3, y: 2.56, w: 7.0, h: 2.85, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 12.5, color: INK_BODY,
      paraSpaceAfter: 7 });""",
    "slide11 left list")

sub("""  card(s, M + 7.9, 1.95, 4.0, 4.3);
  s.addText("NOT YET REAL", { x: M + 8.2, y: 2.16, w: 3.4, h: 0.32,
    isTextBox: true, margin: 0, fontFace: F, fontSize: 12.5, bold: true,
    charSpacing: 1.2, color: AMBER });""",
    """  card(s, M + 7.9, 1.95, 4.0, 3.55);
  tag(s, M + 8.2, 2.16, 3.4, "NOT YET REAL", AMBER);""",
    "slide11 right card")

sub("""    { x: M + 8.2, y: 2.6, w: 3.5, h: 3.4, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 12.5, color: INK_BODY, paraSpaceAfter: 9 });""",
    """    { x: M + 8.2, y: 2.56, w: 3.5, h: 2.85, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 12.5, color: INK_BODY,
      paraSpaceAfter: 9 });""",
    "slide11 right list")

sub("""    { x: M, y: 6.45, w: 11.9, h: 0.4, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 14, italic: true, color: INK_BODY });""",
    """    { x: M, y: 5.78, w: 11.9, h: 0.4, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 14, italic: true, color: INK_BODY });""",
    "slide11 footnote")

# --- slide 4 proof ------------------------------------------------------
sub("""  s.addImage({ path: IMG("docs/brand/proof-cam01.jpg"),
               x: 8.5, y: 1.95, w: 4.1, h: 2.31 });
  s.addText("Live detection: plate located, read and written to the database.",
    { x: 8.5, y: 4.34, w: 4.1, h: 0.6, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 11, color: INK_MUTE });""",
    """  s.addImage({ path: IMG("docs/brand/proof-cam01.jpg"),
               x: 8.35, y: 1.95, w: 4.25, h: 2.39 });
  s.addText("Live detection: plate located, read and written to the database.",
    { x: 8.35, y: 4.44, w: 4.25, h: 0.6, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 11, color: INK_MUTE });""",
    "slide4 image")

# --- slide 9 dark columns ----------------------------------------------
sub("""      { x: cx, y: 4.3, w: cw2 - 0.2, h: 2.4, isTextBox: true, margin: 0,
        fontFace: F, fontSize: 12.5, color: ICE, paraSpaceAfter: 6 });""",
    """      { x: cx, y: 4.26, w: cw2 - 0.2, h: 2.5, isTextBox: true, margin: 0,
        valign: "top", fontFace: F, fontSize: 12.5, color: ICE,
        paraSpaceAfter: 6 });""",
    "slide9 columns")

p.write_text(s, encoding="utf-8")
print("applied:", len(applied))
for a in applied:
    print("  +", a)
if failed:
    print("FAILED:", failed)
print("non-ascii:", sorted({c for c in s if ord(c) > 127}))
