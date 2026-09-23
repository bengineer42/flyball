/**
 * A small markdown renderer for a text tile: headings, paragraphs, bulleted
 * and numbered lists, `code`, **bold**, _italic_ and [links](url). Enough
 * for a note beside the readouts; not a document engine, and no dependency.
 */
import { Fragment, type ReactNode } from "react";

/** An operator- or rig-stored URL is safe to put in an `href`: http(s), mailto, an in-page anchor or a
 * relative path -- never `javascript:`/`data:`/anything else a stored config could smuggle in. Shared
 * with the Link widget, which takes a raw URL the same way. */
export function isSafeHref(href: string): boolean {
  return /^(https?:|mailto:|#|\/)/.test(href);
}

/** Inline marks inside one line. */
function inline(text: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(_[^_]+_)|(\[([^\]]+)\]\(([^)\s]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = key;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const [whole] = m;
    if (m[1]) out.push(<code key={i++}>{whole.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={i++}>{whole.slice(2, -2)}</strong>);
    else if (m[3]) out.push(<em key={i++}>{whole.slice(1, -1)}</em>);
    else if (m[4]) {
      const href = m[6]!;
      const safe = isSafeHref(href);
      out.push(
        <a key={i++} href={safe ? href : undefined} target={href.startsWith("#") ? undefined : "_blank"} rel="noopener noreferrer">
          {m[5]}
        </a>,
      );
    }
    last = m.index + whole.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) {
      i++;
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1]!.length;
      const Tag = (`h${level + 1}`) as "h2" | "h3" | "h4";
      blocks.push(<Tag key={k++}>{inline(heading[2]!)}</Tag>);
      i++;
      continue;
    }
    const bullet = /^\s*([-*]|\d+\.)\s+/.exec(line);
    if (bullet) {
      const ordered = /\d/.test(bullet[1]!);
      const items: ReactNode[] = [];
      while (i < lines.length) {
        const m = /^\s*([-*]|\d+\.)\s+(.*)$/.exec(lines[i]!);
        if (!m) break;
        items.push(<li key={items.length}>{inline(m[2]!)}</li>);
        i++;
      }
      blocks.push(ordered ? <ol key={k++}>{items}</ol> : <ul key={k++}>{items}</ul>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !/^(#{1,3})\s/.test(lines[i]!) && !/^\s*([-*]|\d+\.)\s+/.test(lines[i]!)) para.push(lines[i++]!);
    blocks.push(
      <p key={k++}>
        {para.map((l, j) => (
          <Fragment key={j}>
            {j > 0 && <br />}
            {inline(l, j * 100)}
          </Fragment>
        ))}
      </p>,
    );
  }
  return <div className="fb-markdown">{blocks}</div>;
}
