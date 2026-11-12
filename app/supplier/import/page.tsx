import { ImportPage } from "@/components/supplier/ImportPage";

// `/supplier/import` (P3b B7, X3, X4): admin token typed on the page, kept in memory only.
// Works without a demo cookie — it doesn't use the supplier pages' 401 redirect.
export default function SupplierImport() {
  return <ImportPage />;
}
