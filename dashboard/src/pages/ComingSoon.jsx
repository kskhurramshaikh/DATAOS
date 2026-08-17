export default function ComingSoon({ title }) {
  return (
    <div className="p-7 md:px-8">
      <h1 className="text-xl font-semibold text-ink tracking-tight">{title}</h1>
      <p className="text-[13px] text-ink-faint mt-2 max-w-md">
        Not built yet — this page lands with a later item in the DataOS 3.0 Development Queue,
        once its underlying computation (already signed off) gets its own routed page here.
      </p>
    </div>
  );
}
