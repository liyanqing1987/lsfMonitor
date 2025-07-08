import React, { useState, useRef, useEffect } from 'react';
import { Tooltip } from "@nextui-org/react";

interface TruncatedCellProps {
  content: string | number;
}

const TruncatedCell: React.FC<TruncatedCellProps> = ({ content }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isTruncated, setIsTruncated] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const checkTruncation = () => {
      if (contentRef.current) {
        const { scrollWidth, clientWidth } = contentRef.current;
        setIsTruncated(scrollWidth > clientWidth);
      }
    };

    checkTruncation();

    window.addEventListener('resize', checkTruncation);
    return () => window.removeEventListener('resize', checkTruncation);
  }, [content]);

  if (!isTruncated) {
    return (
      <div
        ref={contentRef}
        className="whitespace-nowrap overflow-hidden text-ellipsis"
        style={{ maxWidth: '200px' }}
      >
        {content}
      </div>
    );
  }

  return (
    <Tooltip content={isExpanded ? "点击收起" : "点击展开"}>
      <div
        ref={contentRef}
        className={`cursor-pointer ${isExpanded ? 'whitespace-normal break-all' : 'whitespace-nowrap overflow-hidden text-ellipsis'}`}
        onClick={() => setIsExpanded(!isExpanded)}
        style={{ maxWidth: '200px' }}
      >
        {content}
      </div>
    </Tooltip>
  );
};

export default TruncatedCell;