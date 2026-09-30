import AutopilotClient from "./AutopilotClient";

export const metadata = { title: "Autopilot" };

export default function AutopilotPage() {
  return (
    <div className="col-span-3 w-full">
      <AutopilotClient />
    </div>
  );
}
