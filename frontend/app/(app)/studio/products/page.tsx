"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ProductsView } from "@/components/views/ProductsView";

function One() {
  return <ProductsView symbol={useSearchParams().get("symbol")} />;
}

export default function Products() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
