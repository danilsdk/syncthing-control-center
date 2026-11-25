const paths = {
  home: "M3 10.5 12 3l9 7.5M5 9.5V21h5v-7h4v7h5V9.5",
  history: "M3 5v5h5 M4 9a8.5 8.5 0 1 1-.4 6 M12 7v5l3.5 2",
  chart: "M4 4v16h17 M8 15l4-5 4 2 4-7",
  search: "M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
  tune: "M3 7h6m5 0h7 M3 17h10m5 0h3 M9 4h5v6H9z M13 14h5v6h-5z",
  pin: "m8 3 9 0-1 6 3 4v2H5v-2l3-4z M12 15v6",
  move: "M2 12h20 M6 8l-4 4 4 4 M18 8l4 4-4 4",
  docs: "M8 3h8l4 4v13H8z M15 3v5h5 M4 7v15h12 M11 12h6 M11 16h4",
  archive: "M3 4h18v5H3z M5 9v11h14V9 M9 13h6",
  photo: "M3 4h18v16H3z M3 16l6-6 5 5 3-3 4 4 M17 8h.01",
  music:
    "M9 17V6l11-3v12 M9 9l11-3 M9 17a3 2 0 1 1-6 0 3 2 0 0 1 6 0 M20 15a3 2 0 1 1-6 0 3 2 0 0 1 6 0",
  code: "m7 7-5 5 5 5 M17 7l5 5-5 5 M14 4l-4 16",
  briefcase: "M3 7h18v13H3z M8 7V4h8v3 M3 12q9 5 18 0 M12 12v3",
  book: "M12 6Q7 3 2 5v14q5-2 10 1 5-3 10-1V5q-5-2-10 1Zm0 0v14",
  folder:
    "M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z",
  server: "M3 4h18v6H3z M3 14h18v6H3z M6 7h.1 M6 17h.1",
  calendar: "M4 5h16v16H4z M4 10h16 M8 3v4 M16 3v4 M8 14h2 M14 14h2 M8 17h2",
  clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z M12 7v5l3 2",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  diagonal: "M6 18 18 6M6 6h12v12",
  back: "M19 12H5m5-5-5 5 5 5",
  down: "m6 9 6 6 6-6",
  add: "M12 5v14M5 12h14",
  edit: "m4 20 5-1L20 8l-4-4L5 15Z M13 7l4 4",
  pause: "M8 5v14M16 5v14",
  play: "m7 4 13 8-13 8z",
  pulse: "M2 12h5l3-8 4 16 3-8h5",
  check: "m4 12 5 5L20 6",
  event: "M5 3h10l4 4v6 M5 3v18h6 M14 3v5h5 M13 17l3 3 5-6",
  close: "m6 6 12 12M18 6 6 18",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  reset: "M4 10a8 8 0 1 1 1 8 M4 4v6h6",
  list: "M4 6h16M4 12h16M4 18h16",
  info: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z M12 11v6 M12 7h.01",
};
export function Icon({ name }: { name: keyof typeof paths }) {
  return (
    <svg
      className="ic"
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[name]} />
    </svg>
  );
}
