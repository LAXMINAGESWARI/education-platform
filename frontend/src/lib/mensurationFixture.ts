const FORMULA_BODIES = [
  "\\text{Required area} = (l + 2w)(b + 2w) - lb\n= 2400 + 3200 + (2 \\times 1200) = 8000 \\, \\text{cm}^2.",
  "\\text{Perimeter} = 2(l + b)\n= 2(40 + 30) = 140 \\, \\text{m}.",
  "\\text{Area of a triangle} = \\tfrac{1}{2} \\times b \\times h\n= \\tfrac{1}{2} \\times 12 \\times 8 = 48 \\, \\text{cm}^2.",
  "\\text{Area of a parallelogram} = b \\times h\n= 15 \\times 6 = 90 \\, \\text{cm}^2.",
  "\\text{Circumference} = 2\\pi r\n= 2 \\times \\tfrac{22}{7} \\times 14 = 88 \\, \\text{cm}.",
  "\\text{Area of a circle} = \\pi r^2\n= \\tfrac{22}{7} \\times 7 \\times 7 = 154 \\, \\text{cm}^2.",
  "\\text{Length of an arc} = \\tfrac{\\theta}{360} \\times 2\\pi r\n= \\tfrac{60}{360} \\times 2 \\times \\tfrac{22}{7} \\times 21 = 22 \\, \\text{cm}.",
  "\\text{Area of a sector} = \\tfrac{\\theta}{360} \\times \\pi r^2\n= \\tfrac{90}{360} \\times \\tfrac{22}{7} \\times 14 \\times 14 = 154 \\, \\text{cm}^2.",
  "\\text{Surface area of a cuboid} = 2(lb + bh + hl)\n= 2(10 \\times 8 + 8 \\times 4 + 4 \\times 10) = 304 \\, \\text{cm}^2.",
  "\\text{Volume of a cuboid} = l \\times b \\times h\n= 10 \\times 8 \\times 4 = 320 \\, \\text{cm}^3.",
  "\\text{Curved surface of a cylinder} = 2\\pi rh\n= 2 \\times \\tfrac{22}{7} \\times 7 \\times 10 = 440 \\, \\text{cm}^2.",
  "\\text{Volume of a cylinder} = \\pi r^2 h\n= \\tfrac{22}{7} \\times 7 \\times 7 \\times 10 = 1540 \\, \\text{cm}^3.",
] as const;

const SECTIONS = [
  "Perimeter",
  "Area of a rectangle",
  "Area of a path",
  "Triangles",
  "Parallelograms",
  "Circles",
  "Arcs and sectors",
  "Surface area",
  "Volume of a cuboid",
  "Cylinders",
  "Cones and spheres",
  "Mixed problems",
] as const;

function prose(section: string, index: number): string {
  return [
    `${section} starts from a measurement you can check on the figure.`,
    `Step ${index + 1} keeps the same units on both sides before you simplify.`,
    "A worked path around a park uses the outer rectangle minus the inner one.",
    "Write the formula, substitute the given lengths, then state the unit.",
  ].join(" ");
}

function table(section: string): string {
  const rows = [
    "| Shape | Length | Breadth | Height | Area |",
    "| --- | --- | --- | --- | --- |",
    ...Array.from({ length: 6 }, (_, index) => {
      const length = 20 + index * 4;
      const breadth = 10 + index * 2;
      return `| ${section} ${index + 1} | ${length} cm | ${breadth} cm | ${index + 2} cm | ${length * breadth} cm² |`;
    }),
  ];
  return rows.join("\n");
}

/** A published mensuration lesson: long prose, several tables, twelve multi-line formulas. */
export function mensurationLessonMarkdown(): string {
  const parts: string[] = ["# Mensuration", ""];
  for (const [index, section] of SECTIONS.entries()) {
    parts.push(`## ${section}`, "");
    const filler = Array.from({ length: 18 }, (_, step) => prose(section, step)).join("\n\n");
    parts.push(filler, "");
    parts.push(`### ${section} measurements`, "", table(section), "");
    const body = FORMULA_BODIES[index] ?? FORMULA_BODIES[0];
    parts.push(`### ${section} formula`, "", `$$\n${body}\n$$`, "");
  }
  return parts.join("\n");
}
