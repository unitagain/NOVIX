import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

function WritingSession() {
  const { projectId } = useParams();
  const navigate = useNavigate();

  useEffect(() => {
    navigate(`/project/${projectId}`);
  }, [projectId, navigate]);

  return (
    <div className="flex items-center justify-center h-screen bg-background text-primary font-mono animate-pulse">
      REDIRECTING_TO_WORKSPACE...
    </div>
  );
}

export default WritingSession;
