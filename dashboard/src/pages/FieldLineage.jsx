import { LineageGraph } from "../components/LineageGraph";

// Field Lineage (Dev Queue item 6, second half). Rendering itself was
// extracted 2026-08-20 into components/LineageGraph.jsx so the same
// graph could also be embedded directly on the Data Catalog page --
// this page now just supplies the full-page chrome (title, intro
// copy) and the full-size graph. See LineageGraph.jsx's own docstring
// for how the underlying data is actually assembled.
export default function FieldLineage() {
  return (
    <div className="p-7 md:px-8">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink tracking-tight">Field Lineage</h1>
        <p className="text-[13px] text-ink-faint mt-1">
          Real dataset → job → dataset lineage, traced from actual S3 storage paths each task read from and wrote to
        </p>
      </div>

      <LineageGraph height={420} />
    </div>
  );
}
