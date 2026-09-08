/**
 * NETRA pitch deck generator.
 *
 * Palette is derived from the product's own brand, not picked from a
 * swatch list: NETRA's console is institutional blue on white, so the
 * deck is deep navy grounds with that same service blue as the accent
 * and the alert red used only where the product uses it  -  for alerts.
 */
const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const IMG = (p) => path.join(ROOT, p);

// --- palette -----------------------------------------------------------
const NAVY = "0B1220";      // dark ground
const NAVY_2 = "141E31";    // dark card
const BLUE = "17509C";      // NETRA service blue (the console accent)
const BLUE_LT = "6FA3DE";   // legible blue on dark
const ICE = "D7E3F4";       // muted text on dark
const WHITE = "FFFFFF";
const CANVAS = "F2F4F7";    // light ground (matches the console canvas)
const INK = "0B0F17";
const INK_BODY = "232A35";
const INK_MUTE = "4A5464";
const LINE = "CBD2DD";
const RED = "A81616";
const AMBER = "8A5406";
const GREEN = "14713A";
const VIOLET = "6D3BC4";

const F = "Calibri";
const FH = "Cambria";       // serif headers, safe-list, true-to-width in QA
const FM = "Courier New";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";              // 13.3 x 7.5 in
pres.author = "Team NETRA";
pres.title = "NETRA  -  ANPR & Crime Tracking Platform";

const W = 13.3, H = 7.5, M = 0.7;

// --- helpers -----------------------------------------------------------
function darkSlide() {
  const s = pres.addSlide();
  s.background = { color: NAVY };
  return s;
}
function lightSlide(title, kicker) {
  const s = pres.addSlide();
  s.background = { color: CANVAS };
  if (kicker) {
    s.addText(kicker.toUpperCase(), {
      x: M, y: 0.42, w: 8, h: 0.24, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 11, bold: true, charSpacing: 1.6, color: BLUE,
    });
  }
  s.addText(title, {
    x: M, y: kicker ? 0.72 : 0.55, w: W - M * 2, h: 0.7, isTextBox: true,
    margin: 0, fontFace: FH, fontSize: 34, bold: true, color: INK,
  });
  return s;
}
// The one repeated motif: a numbered blue disc. Used on every list slide.
function disc(s, n, x, y, color = BLUE) {
  s.addShape(pres.ShapeType.ellipse, {
    x, y, w: 0.44, h: 0.44, fill: { color },
  });
  s.addText(String(n), {
    x, y, w: 0.44, h: 0.44, isTextBox: true, margin: 0,
    align: "center", valign: "middle", fontFace: F, fontSize: 15,
    bold: true, color: WHITE,
  });
}
function card(s, x, y, w, h, fill = WHITE) {
  s.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.04,
    fill: { color: fill }, line: { color: LINE, width: 0.75 },
    shadow: { type: "outer", color: "101828", opacity: 0.08, blur: 8,
              offset: 1, angle: 90 },
  });
}
// A small filled square before a category label. Replaces the coloured
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
function stat(s, x, y, w, value, label, color) {
  s.addText(value, {
    x, y, w, h: 0.78, isTextBox: true, margin: 0, align: "center",
    fontFace: F, fontSize: 44, bold: true, color,
  });
  s.addText(label, {
    x, y: y + 0.78, w, h: 0.5, isTextBox: true, margin: 0, align: "center",
    fontFace: F, fontSize: 12, color: INK_MUTE,
  });
}

// =======================================================================
// 1  -  TITLE
// =======================================================================
{
  const s = darkSlide();
  s.addImage({ path: IMG("docs/brand/netra-icon-512.png"),
               x: M, y: 1.55, w: 1.5, h: 1.5 });
  s.addText("NETRA", {
    x: M, y: 3.25, w: 9, h: 1.25, isTextBox: true, margin: 0,
    fontFace: FH, fontSize: 76, bold: true, color: WHITE, charSpacing: 2,
  });
  s.addText("Sanskrit for \"eye\"", {
    x: M, y: 4.45, w: 9, h: 0.35, isTextBox: true, margin: 0,
    fontFace: F, fontSize: 15, italic: true, color: BLUE_LT,
  });
  s.addText("City-wide number plate recognition and crime tracking", {
    x: M, y: 4.95, w: 10.5, h: 0.45, isTextBox: true, margin: 0,
    fontFace: F, fontSize: 21, color: ICE,
  });
  s.addShape(pres.ShapeType.rect,
    { x: M, y: 5.72, w: 1.4, h: 0.035, fill: { color: BLUE } });
  s.addText("Smart India Hackathon   |   Team NETRA", {
    x: M, y: 6.0, w: 9, h: 0.35, isTextBox: true, margin: 0,
    fontFace: F, fontSize: 13, color: ICE, charSpacing: 1,
  });
  s.addNotes(
    "NETRA means eye in Sanskrit. We read number plates from existing CCTV " +
    "and track vehicles across a city to detect crime. The one thing to " +
    "remember from this deck: we never publish a plate we are not sure about.");
}

// =======================================================================
// 2  -  PROBLEM
// =======================================================================
{
  const s = lightSlide("Cameras everywhere. Almost none of them watching.",
                       "The problem");
  const items = [
    ["Nobody watches in real time",
     "A stolen vehicle passes a camera and nothing happens until someone reviews the tape days later.",
     RED],
    ["Plate cloning is invisible",
     "A criminal copies a legitimate plate onto a second vehicle. No single camera can detect this  -  it only shows up when you compare sightings across cameras and time.",
     AMBER],
    ["OCR alone cannot be trusted",
     "Single-frame plate reading peaks at 95-98% outdoors. At city scale that is thousands of wrong plates a day  -  and a wrong plate puts an innocent citizen under suspicion.",
     VIOLET],
  ];
  let y = 1.95;
  items.forEach(([h1, body, col], i) => {
    card(s, M, y, W - M * 2, 1.42);
    disc(s, i + 1, M + 0.34, y + 0.28, col);
    s.addText(h1, {
      x: M + 1.02, y: y + 0.22, w: 10.6, h: 0.34, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 18, bold: true, color: INK,
    });
    s.addText(body, {
      x: M + 1.02, y: y + 0.63, w: 10.8, h: 0.68, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 13, color: INK_BODY,
    });
    y += 1.62;
  });
  s.addNotes("Three failures. The third is the one that makes ANPR products " +
             "untrustworthy, and it is the one we solve.");
}

// =======================================================================
// 3  -  THE IDEA
// =======================================================================
{
  const s = darkSlide();
  s.addText("OUR CORE IDEA", {
    x: M, y: 0.75, w: 8, h: 0.28, isTextBox: true, margin: 0,
    fontFace: F, fontSize: 12, bold: true, charSpacing: 1.8, color: BLUE_LT,
  });
  s.addText("Temporal Voting", {
    x: M, y: 1.12, w: 11, h: 0.85, isTextBox: true, margin: 0,
    fontFace: FH, fontSize: 44, bold: true, color: WHITE,
  });
  s.addText(
    "One frame gives one guess. A tracked vehicle gives thirty looks at the same plate  -  " +
    "and the errors between frames are independent. Glare in frame 7 is not there in frame 22.",
    { x: M, y: 2.12, w: 11.4, h: 0.7, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 16, color: ICE, lineSpacingMultiple: 1.25 });

  const steps = [
    ["Accumulate", "Every frame of one tracked vehicle votes."],
    ["Weight", "Each vote scaled by plate size, sharpness, angle, detector confidence."],
    ["Combine", "Geometric mean per character  -  one bad character sinks the plate, because one wrong character IS a wrong plate."],
    ["Repair", "Indian plate grammar fixes O/0, I/1 and B/8 by position."],
  ];
  let x = M;
  const cw = (W - M * 2 - 0.3 * 3) / 4;
  steps.forEach(([t, d], i) => {
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 3.15, w: cw, h: 2.35, rectRadius: 0.04,
      fill: { color: NAVY_2 }, line: { color: "24344F", width: 0.75 },
    });
    disc(s, i + 1, x + 0.28, 3.42, BLUE);
    s.addText(t, {
      x: x + 0.28, y: 4.02, w: cw - 0.56, h: 0.36, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 17, bold: true, color: WHITE,
    });
    s.addText(d, {
      x: x + 0.28, y: 4.42, w: cw - 0.56, h: 0.95, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 11.5, color: ICE, lineSpacingMultiple: 1.15,
    });
    x += cw + 0.3;
  });
  s.addText("This is the part that is genuinely ours. Everything else is integration.", {
    x: M, y: 5.75, w: 11.4, h: 0.4, isTextBox: true, margin: 0,
    fontFace: F, fontSize: 14, italic: true, color: BLUE_LT,
  });
  s.addNotes("Emphasise: this is our contribution. YOLO and OCR are off the shelf. " +
             "The voting engine, the plate grammar and the decision policy are ours.");
}

// =======================================================================
// 4  -  PROOF
// =======================================================================
{
  const s = lightSlide("Every frame read it wrong. The consensus was right.",
                       "Proof  -  real Indian footage");
  const rows = [
    ["GXISOGJ   GXI50GJ   GXIS0GJ", "GX15OGJ"],
    ["0G65ZFX   WG65ZFX", "OG65ZFX"],
    ["KHO6KSU   RH06KSU", "KH06KSU"],
  ];
  card(s, M, 1.95, 7.4, 3.15);
  s.addText("WHAT INDIVIDUAL FRAMES READ", {
    x: M + 0.3, y: 2.15, w: 4.2, h: 0.26, isTextBox: true, margin: 0,
    fontFace: F, fontSize: 10, bold: true, charSpacing: 1.4, color: INK_MUTE });
  s.addText("VOTED RESULT", {
    x: M + 5.0, y: 2.15, w: 2.2, h: 0.26, isTextBox: true, margin: 0,
    fontFace: F, fontSize: 10, bold: true, charSpacing: 1.4, color: GREEN });
  let ry = 2.6;
  rows.forEach(([bad, good]) => {
    s.addText(bad, {
      x: M + 0.3, y: ry, w: 4.6, h: 0.4, isTextBox: true, margin: 0,
      fontFace: FM, fontSize: 13, color: RED });
    s.addText(good, {
      x: M + 5.0, y: ry, w: 2.3, h: 0.4, isTextBox: true, margin: 0,
      fontFace: FM, fontSize: 15, bold: true, color: GREEN });
    ry += 0.78;
  });
  s.addImage({ path: IMG("docs/brand/proof-cam01.jpg"),
               x: 8.35, y: 1.95, w: 4.25, h: 2.39 });
  s.addText("Live detection: plate located, read and written to the database.",
    { x: 8.35, y: 4.44, w: 4.25, h: 0.6, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 11, color: INK_MUTE });
  s.addText("Individual reads are noisy because the plate is only ~90 px wide. Voting recovers the truth.",
    { x: M, y: 5.42, w: 11.9, h: 0.4, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 14, italic: true, color: INK_BODY });
  s.addNotes("These are genuine outputs from Indian traffic footage running " +
             "through our pipeline, not mock-ups.");
}

// =======================================================================
// 5  -  THE HONEST CLAIM
// =======================================================================
{
  const s = lightSlide("We never publish a plate we are not sure about.",
                       "The accuracy claim");
  const bands = [
    [">= 0.90", "PUBLISHED AS FACT", GREEN],
    ["0.55 - 0.90", "SENT TO A HUMAN", AMBER],
    ["< 0.55", "DISCARDED", INK_MUTE],
  ];
  let x = M;
  const bw = (W - M * 2 - 0.3 * 2) / 3;
  bands.forEach(([v, l, c]) => {
    card(s, x, 1.95, bw, 1.5);
    s.addText(v, { x, y: 2.12, w: bw, h: 0.55, isTextBox: true, margin: 0,
      align: "center", fontFace: F, fontSize: 30, bold: true, color: c });
    s.addText(l, { x, y: 2.75, w: bw, h: 0.4, isTextBox: true, margin: 0,
      align: "center", fontFace: F, fontSize: 12, bold: true,
      charSpacing: 1.2, color: INK_MUTE });
    x += bw + 0.3;
  });
  s.addText(
    "\"100% accuracy\" is not a model metric  -  it is an operational property. " +
    "We report precision on the auto-accepted set and its coverage, never one without the other.",
    { x: M, y: 3.72, w: 11.9, h: 0.62, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 15, color: INK_BODY, lineSpacingMultiple: 1.2 });

  card(s, M, 4.5, 11.9, 1.75);
  stat(s, M + 0.2, 4.68, 3.6, "48.6%", "single-frame accuracy", RED);
  stat(s, M + 4.0, 4.68, 3.6, "100%", "precision when published", GREEN);
  stat(s, M + 7.8, 4.68, 3.7, "97.5%", "auto-accept coverage", BLUE);
  s.addText("Reproduce:  pytest apps/edge/tests/test_voting.py  -  14 tests, all passing. " +
            "Controlled simulation of the mechanism, not a field figure.",
    { x: M, y: 6.42, w: 11.9, h: 0.4, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 11.5, italic: true, color: INK_MUTE });
  s.addNotes("Be explicit that the 100% is precision on what we choose to " +
             "publish, measured in simulation. Judges respect the distinction.");
}

// =======================================================================
// 6  -  CRIME DETECTION
// =======================================================================
{
  const s = lightSlide("Reading plates is table stakes. The value is the reasoning.",
                       "Crime detection");
  const rules = [
    ["Cloned plate", "The same plate cannot be in two places at once. We compute the implied speed between cameras  -  above what is physically possible, the plate exists twice.", RED],
    ["Speeding", "Average speed over a surveyed camera-to-camera segment. Reported as indicative, not legally defensible.", AMBER],
    ["Loitering / casing", "5+ passes through a zone in 30 minutes, sustained over 8 minutes. Ordinary traffic passes through; it does not circle.", VIOLET],
    ["Watchlist hit", "Exact match plus one-character-off fuzzy  -  the residual OCR error on a stolen vehicle is what you cannot afford to miss.", BLUE],
  ];
  const cw = (W - M * 2 - 0.3) / 2;
  rules.forEach(([t, d, c], i) => {
    const x = M + (i % 2) * (cw + 0.3);
    const y = 1.95 + Math.floor(i / 2) * 1.72;
    card(s, x, y, cw, 1.5);
    disc(s, i + 1, x + 0.3, y + 0.3, c);
    s.addText(t, { x: x + 0.98, y: y + 0.26, w: cw - 1.3, h: 0.34,
      isTextBox: true, margin: 0, fontFace: F, fontSize: 17, bold: true, color: INK });
    s.addText(d, { x: x + 0.98, y: y + 0.64, w: cw - 1.3, h: 0.76,
      isTextBox: true, margin: 0, fontFace: F, fontSize: 12, color: INK_BODY });
  });
  card(s, M, 5.42, 11.9, 0.92, "E9F5EC");
  s.addText("Measured: 327 background sightings -> 0 false positives. 6 alerts, every one real.",
    { x: M + 0.3, y: 5.62, w: 11.3, h: 0.5, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 16, bold: true, color: GREEN });
  s.addNotes("A command centre that cries wolf gets switched off. Zero false " +
             "positives is the number that matters here.");
}

// =======================================================================
// 7  -  ARCHITECTURE
// =======================================================================
{
  const s = lightSlide("How it fits together", "Architecture");
  const stages = [
    ["CAPTURE", "RTSP / video\nOne decoder thread\nper camera", BLUE],
    ["DETECT", "YOLOv8n vehicles\nByteTrack IDs\nYOLO11n plate", VIOLET],
    ["READ + VOTE", "EasyOCR crop\nTemporal voting\nDecision policy", GREEN],
    ["REASON", "Rules engine\nAlerts + evidence\nAudit log", AMBER],
    ["OPERATE", "Live wall | map\nSearch | review\nSSE real time", RED],
  ];
  const cw = (W - M * 2 - 0.22 * 4) / 5;
  stages.forEach(([t, d, c], i) => {
    const x = M + i * (cw + 0.22);
    card(s, x, 2.1, cw, 2.35);
    tag(s, x + 0.2, 2.32, cw - 0.4, t, c, 12);
    s.addText(d, { x: x + 0.2, y: 2.76, w: cw - 0.4, h: 1.45, isTextBox: true,
      margin: 0, valign: "top", fontFace: F, fontSize: 12, color: INK_BODY,
      lineSpacingMultiple: 1.3 });
    if (i < 4) {
      s.addText(">", { x: x + cw + 0.01, y: 3.05, w: 0.2, h: 0.4, isTextBox: true,
        margin: 0, align: "center", fontFace: F, fontSize: 22, bold: true, color: LINE });
    }
  });
  const notes = [
    ["One process, shared models", "Ten separate workers each load their own YOLO and OCR and thrash the machine. Shared: 2.2 GB for the whole network."],
    ["Display decoupled from inference", "Every camera keeps showing smooth video even while inference is busy elsewhere."],
    ["SQLite, PostGIS-ready", "No server, no account, no network  -  the demo survives dead venue wifi."],
  ];
  let x2 = M;
  const nw = (W - M * 2 - 0.3 * 2) / 3;
  notes.forEach(([t, d]) => {
    s.addText(t, { x: x2, y: 4.72, w: nw, h: 0.32, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 13, bold: true, color: BLUE });
    s.addText(d, { x: x2, y: 5.07, w: nw, h: 1.1, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 11.5, color: INK_BODY,
      lineSpacingMultiple: 1.2 });
    x2 += nw + 0.3;
  });
  s.addNotes("Each design decision was forced by measurement, not preference.");
}

// =======================================================================
// 8  -  TECH STACK
// =======================================================================
{
  const s = lightSlide("Technology stack", "What we are using");
  const groups = [
    ["AI / COMPUTER VISION", VIOLET, [
      "YOLOv8n  -  vehicle detection",
      "ByteTrack  -  persistent track IDs",
      "YOLO11n  -  licence plate localisation",
      "EasyOCR  -  plate text recognition",
      "Temporal Voting  -  ours, pure Python",
      "PyTorch CPU / ONNX + DirectML",
    ]],
    ["BACKEND", BLUE, [
      "FastAPI + Uvicorn",
      "Pydantic v2  -  OpenAPI source of truth",
      "SQLite (WAL), PostGIS-ready",
      "Server-Sent Events  -  live feed",
      "Rules engine  -  pure Python",
      "Docker  -  deploy anywhere",
    ]],
    ["FRONTEND", GREEN, [
      "Next.js 15 App Router + React 19",
      "TypeScript",
      "Tailwind CSS v3 over CSS tokens",
      "Leaflet + OpenStreetMap (no key)",
      "Inline SVG charts (no chart lib)",
      "Command palette, keyboard-first",
    ]],
  ];
  let x = M;
  const cw = (W - M * 2 - 0.3 * 2) / 3;
  groups.forEach(([t, c, items]) => {
    card(s, x, 1.95, cw, 3.4);
    tag(s, x + 0.28, 2.18, cw - 0.56, t, c);
    s.addText(items.map((it, i) => ({
      text: it, options: { bullet: true, breakLine: i < items.length - 1 },
    })), { x: x + 0.28, y: 2.6, w: cw - 0.5, h: 2.6, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 12.5, color: INK_BODY,
      paraSpaceAfter: 7 });
    x += cw + 0.3;
  });
  s.addText("Optional cloud AI arbitration (Gemini / OpenAI / Anthropic / Grok)  -  off by default. " +
            "Local models do 100% of per-frame volume; the API is a bounded escalation path.",
    { x: M, y: 5.62, w: 11.9, h: 0.5, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 12, italic: true, color: INK_MUTE });
  s.addNotes("If asked 'did you just wrap an API'  -  local models handle all " +
             "per-frame volume at zero marginal cost. Hosted AI is optional and off.");
}

// =======================================================================
// 9  -  WHAT WE BUILT
// =======================================================================
{
  const s = darkSlide();
  s.addText("WHAT WE BUILT", {
    x: M, y: 0.72, w: 8, h: 0.28, isTextBox: true, margin: 0,
    fontFace: F, fontSize: 12, bold: true, charSpacing: 1.8, color: BLUE_LT });
  s.addText("A working system, not a prototype", {
    x: M, y: 1.08, w: 11.5, h: 0.8, isTextBox: true, margin: 0,
    fontFace: FH, fontSize: 38, bold: true, color: WHITE });

  const nums = [["8,557", "lines of code"], ["12", "database tables"],
                ["25", "API operations"], ["14", "automated tests"]];
  let x = M;
  const nw = (W - M * 2 - 0.3 * 3) / 4;
  nums.forEach(([v, l]) => {
    s.addShape(pres.ShapeType.roundRect, { x, y: 2.2, w: nw, h: 1.35,
      rectRadius: 0.04, fill: { color: NAVY_2 }, line: { color: "24344F", width: 0.75 } });
    s.addText(v, { x, y: 2.36, w: nw, h: 0.65, isTextBox: true, margin: 0,
      align: "center", fontFace: F, fontSize: 36, bold: true, color: WHITE });
    s.addText(l, { x, y: 3.02, w: nw, h: 0.36, isTextBox: true, margin: 0,
      align: "center", fontFace: F, fontSize: 12, color: ICE });
    x += nw + 0.3;
  });

  const cols = [
    ["ELEVEN DASHBOARD SCREENS", [
      "Overview with live KPIs and traffic sparkline",
      "Live camera wall + full-screen camera view",
      "Map with incidents and geofences",
      "Audited plate search",
      "Vehicle trajectory with time playback",
      "Alert triage and evidence view",
      "Human review queue | camera health | audit log",
    ]],
    ["OPERATOR FEATURES", [
      "Ctrl-K command palette",
      "g + key vim-style navigation",
      "Live alert toasts over SSE",
      "Density switch  -  laptop or wall display",
      "Keyboard-first review queue",
      "One-command start, auto-sized to the hardware",
      "Docker deploy to any cloud",
    ]],
  ];
  let cx = M;
  const cw2 = (W - M * 2 - 0.4) / 2;
  cols.forEach(([t, items]) => {
    s.addText(t, { x: cx, y: 3.9, w: cw2, h: 0.32, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 12.5, bold: true, charSpacing: 1.2, color: BLUE_LT });
    s.addText(items.map((it, i) => ({
      text: it, options: { bullet: true, breakLine: i < items.length - 1 } })),
      { x: cx, y: 4.26, w: cw2 - 0.2, h: 2.5, isTextBox: true, margin: 0,
        valign: "top", fontFace: F, fontSize: 12.5, color: ICE,
        paraSpaceAfter: 6 });
    cx += cw2 + 0.4;
  });
  s.addNotes("Everything listed here runs. The status slide names what does not.");
}

// =======================================================================
// 10  -  PRIVACY
// =======================================================================
{
  const s = lightSlide("It is a surveillance system. We engineered it like one.",
                       "Privacy & accountability");
  const items = [
    ["No owner PII in the sighting stream", "Plate -> owner is a separate, audited lookup. A plate search cannot silently become an identity lookup."],
    ["Every search is logged before results return", "Operator, query and stated reason. \"Who searched this plate, and why\" is the most important record in the system."],
    ["Watchlist entries expire", "Purpose-bound with an expiry date. An entry that never expires is how a watchlist becomes permanent surveillance."],
    ["Alerts are advisory  -  a human always decides", "No automated enforcement, ever. Cloned-plate alerts need high confidence on both sightings."],
  ];
  let y = 1.95;
  items.forEach(([t, d], i) => {
    card(s, M, y, W - M * 2, 1.05);
    disc(s, i + 1, M + 0.32, y + 0.3, BLUE);
    s.addText(t, { x: M + 1.0, y: y + 0.16, w: 10.6, h: 0.34, isTextBox: true,
      margin: 0, fontFace: F, fontSize: 16, bold: true, color: INK });
    s.addText(d, { x: M + 1.0, y: y + 0.53, w: 10.8, h: 0.42, isTextBox: true,
      margin: 0, fontFace: F, fontSize: 12, color: INK_BODY });
    y += 1.2;
  });
  s.addText("Framed against India's DPDP Act 2023.",
    { x: M, y: 6.75, w: 11.9, h: 0.35, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 13, italic: true, color: INK_MUTE });
  s.addNotes("This slide wins trust. Most ANPR pitches ignore privacy entirely.");
}

// =======================================================================
// 11  -  HONEST STATUS
// =======================================================================
{
  const s = lightSlide("What works, and what does not", "Honest status");
  card(s, M, 1.95, 7.6, 3.55);
  tag(s, M + 0.3, 2.16, 5, "WORKING TODAY", GREEN);
  const works = [
    "Temporal voting + Indian plate grammar (unit tested)",
    "Rules engine  -  0 false positives on 327 sightings",
    "REST API, SSE live feed, audit log",
    "Dashboard, map, trajectory, review queue",
    "Real ANPR on Indian footage  -  publishes real plates",
    "Multi-camera live wall with CCTV overlay",
    "Auto-sizing to available hardware",
  ];
  s.addText(works.map((t, i) => ({ text: t,
    options: { bullet: true, breakLine: i < works.length - 1 } })),
    { x: M + 0.3, y: 2.56, w: 7.0, h: 2.85, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 12.5, color: INK_BODY,
      paraSpaceAfter: 7 });

  card(s, M + 7.9, 1.95, 4.0, 3.55);
  tag(s, M + 8.2, 2.16, 3.4, "NOT YET REAL", AMBER);
  const gaps = [
    "Video anomaly detection  -  ingest and alerting done, the detector is not trained",
    "Cloud AI arbitration  -  code complete, off by default, needs an API key",
    "Threshold calibration on a labelled Indian dataset",
  ];
  s.addText(gaps.map((t, i) => ({ text: t,
    options: { bullet: true, breakLine: i < gaps.length - 1 } })),
    { x: M + 8.2, y: 2.56, w: 3.5, h: 2.85, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 12.5, color: INK_BODY,
      paraSpaceAfter: 9 });

  s.addText("A demo that claims more than it does is a demo that dies under questioning.",
    { x: M, y: 5.78, w: 11.9, h: 0.4, isTextBox: true, margin: 0,
      valign: "top", fontFace: F, fontSize: 14, italic: true, color: INK_BODY });
  s.addNotes("Volunteering the gaps buys credibility for everything else on " +
             "the working side.");
}

// =======================================================================
// 12  -  CLOSE
// =======================================================================
{
  const s = darkSlide();
  s.addImage({ path: IMG("docs/brand/netra-icon-512.png"),
               x: M, y: 1.1, w: 1.15, h: 1.15 });
  s.addText("NETRA", { x: M, y: 2.4, w: 8, h: 0.85, isTextBox: true, margin: 0,
    fontFace: FH, fontSize: 50, bold: true, color: WHITE, charSpacing: 1.5 });
  s.addText("The system never publishes a plate it is not sure about.",
    { x: M, y: 3.4, w: 11.5, h: 0.6, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 25, color: WHITE });
  s.addText(
    "That single property is what separates an ANPR demo from an ANPR system a police " +
    "force could actually be given. Everything else  -  the cross-camera cloning detection, " +
    "the audited search, the human review queue  -  follows from taking it seriously.",
    { x: M, y: 4.2, w: 11.5, h: 1.1, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 15, color: ICE, lineSpacingMultiple: 1.3 });
  s.addShape(pres.ShapeType.rect,
    { x: M, y: 5.55, w: 1.4, h: 0.035, fill: { color: BLUE } });
  s.addText("python scripts/start.py   ->   localhost:3000",
    { x: M, y: 5.85, w: 9, h: 0.4, isTextBox: true, margin: 0,
      fontFace: FM, fontSize: 14, color: BLUE_LT });
  s.addText("Smart India Hackathon   |   Team NETRA",
    { x: M, y: 6.5, w: 9, h: 0.35, isTextBox: true, margin: 0,
      fontFace: F, fontSize: 12, color: ICE, charSpacing: 1 });
  s.addNotes("Close on the operational property. Then offer the live demo.");
}

const OUT = path.join(ROOT, "docs", "NETRA-Pitch-Deck.pptx");
pres.writeFile({ fileName: OUT }).then(() => {
  console.log("wrote " + OUT);
  console.log("size " + (fs.statSync(OUT).size / 1024).toFixed(0) + " KB");
});
