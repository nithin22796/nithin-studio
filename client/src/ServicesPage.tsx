import { Link } from "react-router-dom";
import { services } from "./services";

export function ServicesPage() {
  return (
    <>
      <h2>Services</h2>
      <ul className="service-list">
        {services.map((service) => (
          <li key={service.name} className="service-card">
            <Link
              to={`/services/${service.name}`}
              className="service-card-link"
            >
              <span className="service-status">● online</span>
              <div className="service-name">{service.name}</div>
              <p className="service-description">{service.description}</p>
              <span className="service-route">/services/{service.name}</span>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
