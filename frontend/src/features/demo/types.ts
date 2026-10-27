export interface DemoSession {
  enabled: boolean;
  workspace_id: string;
  template_version: string;
  review_required: boolean;
  searches: { limit: 2; used: number; remaining: number };
  global_search_available: boolean;
  capabilities: {
    review_business: boolean; maps_search: boolean; prepare_draft: boolean;
    paid_research: boolean; manual_evaluation: boolean; playbook_generation: boolean;
    outbound_sending: boolean; administration: boolean;
  };
}
