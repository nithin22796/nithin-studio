import { Link, useParams } from "react-router-dom";
import { services } from "./services";

export function ServiceDetailPage() {
  const { name } = useParams();
  const service = services.find((s) => s.name === name);

  return (
    <div className="app-placeholder">
      <Link className="back-link" to="/services">
        ← Services
      </Link>
      {service ? (
        <>
          <h2>{service.name}</h2>
          <p>{service.description}</p>
          <div className="placeholder-box">Coming soon</div>
        </>
      ) : (
        <>
          <h2>Not found</h2>
          <p>No service named "{name}".</p>
        </>
      )}
    </div>
  );
}
