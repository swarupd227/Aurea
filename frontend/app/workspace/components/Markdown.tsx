import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`astra-md ${className}`}>
      <ReactMarkdown
        // "~" means "about" in a figure. Two of them must not strike out the words between.
        remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
        components={{
          a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
