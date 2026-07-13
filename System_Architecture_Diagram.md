flowchart TB
    classDef portal fill:#DCE6F1,stroke:#1F3864,stroke-width:1px,color:#1a1a1a;
    classDef logic fill:#DCEEEE,stroke:#2E8B8B,stroke-width:1px,color:#1a1a1a;
    classDef data fill:#F2F2F2,stroke:#595959,stroke-width:1px,color:#1a1a1a;

    subgraph P["Presentation Layer — App Builder Pages"]
        CP["Customer Portal<br/>Upload PO · Download Invoice · Upload Revision"]
        SP["Staff / Operations Portal<br/>Ticket List · Audit Panel · Upload Quotation"]
        AD["Admin Dashboard<br/>KPI Cards · Charts · Discrepancy Log"]
    end

    subgraph L["Logic & Integration Layer — App Resources"]
        IG["JS Object<br/>Invoice Generator"]
        EA["Email Alert Engine"]
        VC["JS Object<br/>Version Controller"]
        AI["AI Analyzer<br/>(REST API)"]
        ERP["ERP / MES Integration"]
    end

    subgraph D["Data Storage Layer — App Resources"]
        TK[("PO_Q_Audit_Ticket<br/>Ticket Resource · Multi-Status")]
        AH[("Amendment_History<br/>Table Resource")]
    end

    CP -->|uploads PO / revision| IG
    IG -->|generates invoice PDF| CP
    IG -->|writes ticket record| TK
    IG -->|triggers alert| EA
    EA -->|notifies via ticket link| SP

    SP -->|uploads quotation, runs audit| AI
    AI -->|writes discrepancy result| TK
    AI -->|logs analysis| AH

    SP -->|confirm / request amendment| ERP
    SP -->|amendment triggers revision| VC
    VC -->|increments version, logs record| AH

    ERP -->|auto-creates sales order| TK

    TK -->|ticket & status data| AD
    AH -->|version & audit history| AD

    class CP,SP,AD portal
    class IG,EA,VC,AI,ERP logic
    class TK,AH data
