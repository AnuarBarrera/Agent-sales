import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "../components/Layout";
import { LoginPage } from "../features/auth/LoginPage";
import { ProspectionNewPage } from "../features/prospection/ProspectionNewPage";
import { ProspectionRunPage } from "../features/prospection/ProspectionRunPage";
import { LeadsListPage } from "../features/leads/LeadsListPage";
import { LeadDetailPage } from "../features/leads/LeadDetailPage";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { PipelinePage } from "../features/pipeline/PipelinePage";
import { LeadImportPage } from "../features/leads/LeadImportPage";
import { AuthProvider } from "../features/auth/useAuth";
import { AdminRoute } from "../features/admin/AdminRoute";
import { AdminDashboardPage } from "../features/admin/AdminDashboardPage";
import { TeamPage } from "../features/admin/TeamPage";
import { AssignmentsPage } from "../features/admin/AssignmentsPage";
import { SellerProfilePage } from "../features/sellerProfile/SellerProfilePage";

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route path="/" element={<Navigate to="/leads" replace />} />
          <Route path="/prospection/new" element={<ProspectionNewPage />} />
          <Route path="/prospection/:runId" element={<ProspectionRunPage />} />
          <Route path="/leads" element={<LeadsListPage />} />
          <Route path="/leads/import" element={<LeadImportPage />} />
          <Route path="/leads/:leadId" element={<LeadDetailPage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/seller-profile" element={<SellerProfilePage />} />
          <Route path="/admin" element={<AdminRoute><AdminDashboardPage /></AdminRoute>} />
          <Route path="/admin/team" element={<AdminRoute><TeamPage /></AdminRoute>} />
          <Route path="/admin/assignments" element={<AdminRoute><AssignmentsPage /></AdminRoute>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}

export default App;
