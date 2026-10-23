import type { ComponentProps } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/** Agent text is Markdown: GitHub's flavor (bare URLs become links, tables, strikethrough, task lists), with every
 *  link opening in a new tab so the build stays open. Unsafe URLs are dropped by react-markdown itself. */
const LINKS: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={LINKS}>
      {children}
    </ReactMarkdown>
  );
}

const INLINE: Components = {
  ...LINKS,
  p: ({ children }: ComponentProps<"p">) => <>{children}</>,
};

/** Agent text inside a line of the UI (a list item, a criterion, a question): rendered in place, without the
 *  paragraph a block would add. */
export function Inline({ children }: { children: string }) {
  return (
    <span className="md md--inline">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={INLINE}>
        {children}
      </ReactMarkdown>
    </span>
  );
}
