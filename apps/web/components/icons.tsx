/** Monoline 16px icons.
 *
 *  Hand-drawn rather than pulled from an icon package: the console needs
 *  eleven glyphs, and a library would ship hundreds plus its own stroke
 *  and sizing conventions to fight. One stroke width, one grid, one size.
 */
const S = {
  width: 16, height: 16, viewBox: "0 0 16 16", fill: "none",
  stroke: "currentColor", strokeWidth: 1.5,
  strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const IconOverview = () => (
  <svg {...S}><rect x="2" y="2" width="5" height="5" rx="1" /><rect x="9" y="2" width="5" height="5" rx="1" /><rect x="2" y="9" width="5" height="5" rx="1" /><rect x="9" y="9" width="5" height="5" rx="1" /></svg>
);
export const IconLive = () => (
  <svg {...S}><rect x="1.5" y="3.5" width="9" height="9" rx="1.5" /><path d="M10.5 7l4-2.2v6.4L10.5 9z" /></svg>
);
export const IconMap = () => (
  <svg {...S}><path d="M1.5 4l4-1.5 5 2 4-1.5v9l-4 1.5-5-2L1.5 13z" /><path d="M5.5 2.5v9M10.5 4.5v9" /></svg>
);
export const IconAlert = () => (
  <svg {...S}><path d="M8 1.8l6.2 11.4H1.8z" /><path d="M8 6.4v3M8 11.4h.01" /></svg>
);
export const IconSearch = () => (
  <svg {...S}><circle cx="7" cy="7" r="4.6" /><path d="M10.4 10.4l3.1 3.1" /></svg>
);
export const IconFace = () => (
  <svg {...S}><circle cx="8" cy="8" r="6.2" /><circle cx="6" cy="6.8" r=".6" fill="currentColor" stroke="none" /><circle cx="10" cy="6.8" r=".6" fill="currentColor" stroke="none" /><path d="M5.6 10.2a3.2 3.2 0 004.8 0" /></svg>
);
export const IconWatchlist = () => (
  <svg {...S}><circle cx="6" cy="5.5" r="2.6" /><path d="M1.8 13.6a4.6 4.6 0 018.4 0" /><path d="M11.4 5.5h3M12.9 4v3" /></svg>
);
export const IconReview = () => (
  <svg {...S}><rect x="2.2" y="2.2" width="11.6" height="11.6" rx="1.6" /><path d="M5.2 8.2l2 2 3.6-4" /></svg>
);
export const IconCamera = () => (
  <svg {...S}><path d="M1.8 5.2h2.6l1-1.6h5.2l1 1.6h2.6v8H1.8z" /><circle cx="8" cy="9" r="2.4" /></svg>
);
export const IconAudit = () => (
  <svg {...S}><path d="M3.5 1.8h6l3 3v9.4h-9z" /><path d="M9.2 1.8v3.2h3.2M5.6 8.4h4.8M5.6 11h3.2" /></svg>
);
export const IconChevron = ({ open }: { open?: boolean }) => (
  <svg {...S} width={14} height={14} viewBox="0 0 16 16"
       style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 140ms" }}>
    <path d="M6 3.5L10.5 8 6 12.5" />
  </svg>
);
export const IconBack = () => (
  <svg {...S}><path d="M13 8H3.5" /><path d="M7 3.5L2.5 8 7 12.5" /></svg>
);
export const IconForward = () => (
  <svg {...S}><path d="M3 8h9.5" /><path d="M9 3.5L13.5 8 9 12.5" /></svg>
);
export const IconPanel = () => (
  <svg {...S}><rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.5" /><path d="M6.2 2.8v10.4" /></svg>
);
