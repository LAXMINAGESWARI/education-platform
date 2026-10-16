import { Children, isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import { normalizeMarkdownMath } from "../lib/markdownMath";
import { MermaidDiagram } from "./MermaidDiagram";

type MarkdownContentProps = {
  children: string;
  className?: string;
  inline?: boolean;
};

function fenceLanguage(className: string | undefined): string | undefined {
  return /language-([\w-]+)/i.exec(className ?? "")?.[1]?.toLowerCase();
}

function isMermaidDiagram(node: ReactNode): boolean {
  return isValidElement(node) && node.type === MermaidDiagram;
}

function markdownComponents(inline: boolean): Components {
  return {
    ...(inline
      ? {
          p: ({ children: nodes }) => <>{nodes}</>,
        }
      : {}),
    pre({ children }) {
      const nested = Children.toArray(children);
      if (nested.length === 1 && isMermaidDiagram(nested[0])) {
        return nested[0];
      }
      return <pre>{children}</pre>;
    },
    code({ className, children, node: _node, ...props }) {
      if (fenceLanguage(className) === "mermaid") {
        return <MermaidDiagram chart={String(children).replace(/\n$/, "")} />;
      }
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    },
  };
}

export function MarkdownContent({
  children,
  className,
  inline = false,
}: MarkdownContentProps) {
  const classes = ["markdown-content", className].filter(Boolean).join(" ");
  const Tag = inline ? "span" : "div";
  return (
    <Tag className={classes}>
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }]]}
        components={markdownComponents(inline)}
      >
        {normalizeMarkdownMath(children)}
      </ReactMarkdown>
    </Tag>
  );
}
