import { redirect } from "next/navigation";

/** The console has no landing page — an operator opening NETRA wants the
 *  overview, not a marketing screen. */
export default function Home() {
  redirect("/dashboard");
}
