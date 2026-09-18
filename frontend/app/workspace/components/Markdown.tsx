import ReactMarkdown from "react-markdown";

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`astra-md ${className}`}>
      <ReactMarkdown
        components={{
          a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
