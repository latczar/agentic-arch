import { useEffect, useState } from "react";

export interface Section {
  id: string;
  label: string;
}

// How far below the top of the window a section counts as the one being read.
// A little more than the height of the bar itself.
const READING_LINE = 120;

/**
 * Where everything on a long page is, kept in view.
 *
 * A result runs to several screens: the answer, every step, the questions and
 * the time. Without this the questions, which are the one place the reader is
 * asked to do something, sit below a column of verdicts that nobody scrolls
 * past. It also says where you are, so a jump never leaves you lost.
 */
export function Contents({ sections }: { sections: Section[] }) {
  const ids = sections.map((s) => s.id).join(" ");
  const [current, setCurrent] = useState(sections[0]?.id);

  useEffect(() => {
    const list = ids.split(" ");
    let frame = 0;

    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        let active = list[0];
        for (const id of list) {
          const element = document.getElementById(id);
          if (element && element.getBoundingClientRect().top <= READING_LINE) active = id;
        }
        // A short last section never reaches the reading line, so at the very
        // bottom of the page it is the one being read whatever its position.
        const atBottom =
          window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
        setCurrent(atBottom ? list[list.length - 1] : active);
      });
    };

    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [ids]);

  return (
    <nav className="contents" aria-label="On this page">
      {sections.map(({ id, label }) => (
        <a
          key={id}
          href={`#${id}`}
          className={`contents__link ${id === current ? "contents__link--current" : ""}`}
          aria-current={id === current ? "location" : undefined}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}
